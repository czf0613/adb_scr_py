import os
import tempfile
from asyncio import Lock
from importlib.resources import files as resource_files

from aiofiles import open as aio_open
from aiofiles.os import makedirs, remove
from aiofiles.ospath import exists, isfile

from . import consts, exceptions
from .adb_cmd.base import adb_devices, adb_version, kill_adb_daemon, start_adb_daemon
from .device.android_device import AndroidDevice, GestureAction, GestureActionNode
from .device.options import ConnectionOptions
from .exceptions import AdbScrPyInitException
from .logger import logger

__all__ = [
    "init_lib",
    "deinit_lib",
    "list_devices",
    "set_screen_record_fps",
    "AndroidDevice",
    "ConnectionOptions",
    "GestureAction",
    "GestureActionNode",
    "exceptions",
]

DAEMON_RUNNING = False
_mutex = Lock()


async def init_lib(adb_path: str | None = None) -> tuple[str, str]:
    """初始化库并启动全局 ADB daemon。

    Args:
        adb_path: ADB 可执行文件路径；None 使用 PATH 中的 adb。

    Returns:
        (ADB 版本, scrcpy 版本)。重复初始化返回已有版本信息。

    Raises:
        AdbScrPyInitException: 指定路径不存在或 daemon 启动返回非零状态。
        OSError: 文件操作或子进程启动失败。
        asyncio.TimeoutError: ADB 命令超时。

    Notes:
        同一事件循环内的初始化调用由锁串行化。
    """
    async with _mutex:
        global DAEMON_RUNNING
        if DAEMON_RUNNING:
            return consts.ADB_VERSION, consts.SCRCPY_VERSION

        # 如果指定了adb_path，则使用指定的路径
        if adb_path is not None:
            if not await exists(adb_path):
                logger.error(f"ADB可执行文件不存在：{adb_path}")
                raise AdbScrPyInitException(f"ADB可执行文件不存在：{adb_path}")
            consts.ADB_PATH = adb_path

        # 释放scrcpy-server.bin到临时目录
        temp_dir = os.path.join(tempfile.gettempdir(), "adb_scr_py")
        await makedirs(temp_dir, exist_ok=True)
        consts.SCRCPY_SERVER_PATH = os.path.join(temp_dir, "scrcpy-server.bin")
        res_file = (
            resource_files("adb_scr").joinpath("res/scrcpy-server.bin").read_bytes()
        )
        async with aio_open(consts.SCRCPY_SERVER_PATH, "wb") as f:
            await f.write(res_file)
        logger.info(f"scrcpy-server.bin已释放到：{consts.SCRCPY_SERVER_PATH}")

        # 获取adb版本号
        consts.ADB_VERSION = await adb_version()

        # 启动adb守护进程
        return_code = await start_adb_daemon()
        if return_code != 0:
            logger.error(f"启动ADB守护进程失败，状态码：{return_code}")
            raise AdbScrPyInitException(f"启动ADB守护进程失败，状态码：{return_code}")
        DAEMON_RUNNING = True
        logger.info(f"ADB守护进程已启动，版本号：{consts.ADB_VERSION}")

        return consts.ADB_VERSION, consts.SCRCPY_VERSION


async def deinit_lib() -> None:
    """停止全局 ADB daemon 并删除释放到临时目录的服务端文件。

    Raises:
        OSError: 文件操作或子进程启动失败。
        asyncio.TimeoutError: 停止 ADB daemon 超时。

    Notes:
        必须先 await 每台设备的 disconnect()。此函数不会代替调用方关闭
        AndroidDevice；停止共享 daemon 也会影响其他 ADB 客户端。
    """
    async with _mutex:
        global DAEMON_RUNNING
        if not DAEMON_RUNNING:
            return

        # 删掉临时文件
        if await isfile(consts.SCRCPY_SERVER_PATH):
            await remove(consts.SCRCPY_SERVER_PATH)
            logger.info(f"已删除临时文件：{consts.SCRCPY_SERVER_PATH}")

        await kill_adb_daemon()
        DAEMON_RUNNING = False
        logger.info("ADB守护进程已停止")


async def list_devices() -> list[str]:
    """获取 ADB 返回的设备序列号列表。

    Returns:
        设备序列号或网络地址列表；未初始化或命令返回失败时为空列表。
        结果可能包含 offline 或 unauthorized 设备，不能视为可连接性保证。

    Raises:
        OSError: 无法启动 ADB。
        asyncio.TimeoutError: ADB 命令超时。
    """
    if not DAEMON_RUNNING:
        logger.warning("ADB守护进程未启动，无法获取设备列表")
        return []

    return await adb_devices()


def set_screen_record_fps(fps: int) -> None:
    """设置后续新会话的录屏帧率上限。

    Args:
        fps: 10 到 60 的整数，默认配置为 30；不接受 bool 或小数。

    Notes:
        非法值会记录警告并保持原值。设置作用于整个进程，仅在下一次启动
        scrcpy server 时读取；不会修改已运行的会话，也不保证实际帧率。
    """
    if type(fps) is not int or fps < 10 or fps > 60:
        logger.warning("建议录屏帧率在[10, 60]之间，当前设置值不生效")
        return

    consts.SCREEN_FPS = fps
    logger.info(f"录屏帧率已设置为：{fps}")
