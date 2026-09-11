import asyncio
from asyncio import subprocess

from aiofiles.ospath import isfile

from .. import consts
from ..async_utils import complete_on_cancel, stop_process
from ..logger import logger
from .base import adb_device_cmd

__all__ = []


async def push_file(serial: str, local_path: str, remote_path: str) -> bool:
    """推送文件到ADB设备。

    Args:
        local_path: 本地文件路径。
        remote_path: 设备目标路径。

    Returns:
        是否成功。
    """
    if not await isfile(local_path):
        logger.error(f"本地文件不存在：{local_path}")
        return False

    return await adb_device_cmd(serial, "push", local_path, remote_path)


async def launch_app(serial: str, package_name: str, activity_name: str) -> bool:
    """启动ADB设备上的应用。

    Args:
        package_name: 应用包名。
        activity_name: 应用主活动名。

    Returns:
        是否成功。
    """
    component = f"{package_name}/{activity_name}"
    return await adb_device_cmd(serial, "shell", "am", "start", "-n", component)


async def stop_app(serial: str, package_name: str) -> bool:
    """停止ADB设备上的应用。

    Args:
        package_name: 应用包名。

    Returns:
        是否成功。
    """
    return await adb_device_cmd(serial, "shell", "am", "force-stop", package_name)


async def start_scrcpy_server(
    serial: str, scid: str, *, android_api_level: int = 0
) -> subprocess.Process | None:
    """启动ADB设备上的scrcpy服务器。

    Args:
        scid: scrcpy会话ID。
        android_api_level: 已查询的 Android API 级别；>=33 保留手机外放，
            30–32 使用 output，低于 30 关闭音频。

    Returns:
        scrcpy服务器进程对象，若启动失败则为None。
    """
    process = None
    transferred = False
    audio_options = ["audio=false"]
    if android_api_level >= 30:
        duplicate = android_api_level >= 33
        audio_options = [
            "audio=true",
            "audio_codec=aac",
            "audio_source=playback" if duplicate else "audio_source=output",
            f"audio_dup={str(duplicate).lower()}",
        ]

    async def spawn():
        nonlocal process
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
            *audio_options,
            "control=true",
            "cleanup=false",
            stdin=subprocess.DEVNULL,
        )

    try:
        await complete_on_cancel(spawn())
        # Preserve the existing startup buffer; socket readiness is checked later.
        await asyncio.sleep(2)
        if process.returncode is not None:
            logger.error(f"启动 scrcpy 失败，状态码：{process.returncode}")
            return None
        transferred = True
        return process
    finally:
        if process is not None and not transferred:
            await complete_on_cancel(stop_process(process))
