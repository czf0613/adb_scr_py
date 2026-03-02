from aiofiles.ospath import isfile
from ..logger import logger
from .base import adb_device_cmd
from asyncio import subprocess
import asyncio
from .. import consts


async def push_file(serial: str, local_path: str, remote_path: str) -> bool:
    """
    推送文件到ADB设备。
    :param local_path: 本地文件路径。
    :param remote_path: 设备目标路径。
    :return: 是否成功。
    """
    if not await isfile(local_path):
        logger.error(f"本地文件不存在：{local_path}")
        return False

    return await adb_device_cmd(serial, "push", local_path, remote_path)


async def launch_app(serial: str, package_name: str, activity_name: str) -> bool:
    """
    启动ADB设备上的应用。
    :param package_name: 应用包名。
    :param activity_name: 应用主活动名。
    :return: 是否成功。
    """
    component = f"{package_name}/{activity_name}"
    return await adb_device_cmd(serial, "shell", "am", "start", "-n", component)


async def stop_app(serial: str, package_name: str) -> bool:
    """
    停止ADB设备上的应用。
    :param package_name: 应用包名。
    :return: 是否成功。
    """
    return await adb_device_cmd(serial, "shell", "am", "force-stop", package_name)


async def start_scrcpy_server(serial: str, scid: str) -> subprocess.Process | None:
    """
    启动ADB设备上的scrcpy服务器。
    :param scid: scrcpy会话ID。
    :return: scrcpy服务器进程对象，若启动失败则为None。
    """
    process = await subprocess.create_subprocess_exec(
        consts.ADB_PATH,
        "-s",
        serial,
        "shell",
        f"CLASSPATH={consts.SCRCPY_PATH_ON_DEVICE}",
        "app_process",
        "/",
        "com.genymobile.scrcpy.Server",
        consts.SCRCPY_VERSION,
        f"scid={scid}",
        "tunnel_forward=true",
        "stay_awake=true",
        f"max_fps={consts.SCREEN_FPS}",
        "video=true",
        "video_codec=h264",
        "audio=false",
        "control=true",
        "cleanup=false",
        stdin=subprocess.DEVNULL,
    )

    # 等待上线
    await asyncio.sleep(2)

    if process.returncode is not None:
        logger.error(f"启动scrcpy服务器失败，状态码：{process.returncode}")
        return None

    return process
