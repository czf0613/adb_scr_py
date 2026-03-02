from asyncio import Lock
from aiofiles.ospath import exists
from .logger import logger
from .exceptions import AdbScrPyInitException
import os
import tempfile
from importlib.resources import files as resource_files
from aiofiles import open as aio_open
from .adb_cmd.base import adb_version, start_adb_daemon, kill_adb_daemon, adb_devices
from . import consts
from .device.android_device import AndroidDevice, GestureAction, GestureActionNode

__all__ = [
    "init_lib",
    "deinit_lib",
    "list_devices",
    "AndroidDevice",
    "GestureAction",
    "GestureActionNode",
]

DAEMON_RUNNING = False
_mutex = Lock()


async def init_lib(adb_path: str | None = None) -> tuple[str, str]:
    """
    初始化库。会启动好守护进程等一系列准备工作，然后返回版本信息。
    这个函数有并发保护，调用方可以并发调用，但是只有第一次调用会生效。

    :param adb_path: ADB可执行文件路径。如果为None，则会使用系统中查找。
    :raises AdbScrPyInitException: 初始化失败。
    :return: [ADB版本号, scrcpy版本号]
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
        os.makedirs(temp_dir, exist_ok=True)
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
    """
    反初始化库。会停止守护进程等一系列清理工作。
    这个函数有并发保护，调用方可以并发调用，但是只有第一次调用会生效。
    不会抛出异常，永远认为执行正确。
    """
    async with _mutex:
        global DAEMON_RUNNING
        if not DAEMON_RUNNING:
            return

        # 删掉临时文件
        if os.path.isfile(consts.SCRCPY_SERVER_PATH):
            os.remove(consts.SCRCPY_SERVER_PATH)
            logger.info(f"已删除临时文件：{consts.SCRCPY_SERVER_PATH}")

        await kill_adb_daemon()
        DAEMON_RUNNING = False
        logger.info("ADB守护进程已停止")


async def list_devices() -> list[str]:
    """
    获取当前连接的ADB设备列表。
    :return: 设备列表，每个元素为设备地址/序列号。
    """
    if not DAEMON_RUNNING:
        logger.warning("ADB守护进程未启动，无法获取设备列表")
        return []

    return await adb_devices()


def set_screen_record_fps(fps: int) -> None:
    """
    设置录屏的帧率。目前默认是30，允许设置为[10, 60]之间的整数。
    注意，这个函数只是设置了一个变量，实际生效需要等下一次connect的时候才会生效。
    有硬件解码器的平台不需要管这个值，性能肯定够用，其它平台建议酌情处理。
    """
    if fps < 10 or fps > 60:
        logger.warning(f"建议录屏帧率在[10, 60]之间，当前设置值不生效")
        return

    consts.SCREEN_FPS = fps
    logger.info(f"录屏帧率已设置为：{fps}")
