import asyncio
import math
import os
import tempfile
from asyncio import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from aiofiles.ospath import isfile

from .. import consts
from ..async_utils import complete_on_cancel, stop_process
from ..logger import logger
from .base import adb_device_cmd

__all__ = []


def _check_transfer(remote_path: str, timeout: float) -> None:
    if not isinstance(remote_path, str):
        raise TypeError("remote_path 必须是字符串")
    if not remote_path.startswith("/") or "\0" in remote_path:
        raise ValueError("remote_path 必须是无 NUL 的 Android 绝对路径")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("timeout 必须是秒数")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout 必须是有限正数")


def _local_path(path: str | os.PathLike[str]) -> str:
    value = os.fspath(path)
    if not isinstance(value, str):
        raise TypeError("本地路径必须是 str 或 PathLike[str]")
    if not value or "\0" in value:
        raise ValueError("本地路径不能为空或包含 NUL")
    # Absolute paths cannot be mistaken for adb options; no shell is involved.
    return os.path.abspath(value)


@asynccontextmanager
async def _transfer_file():
    directory = None

    async def create():
        nonlocal directory
        directory = await asyncio.to_thread(
            tempfile.TemporaryDirectory, prefix="adb-scr-transfer-"
        )

    try:
        # Keep ownership even if cancellation arrives while mkdtemp is running.
        await complete_on_cancel(create())
        yield Path(directory.name) / "payload"
    finally:
        if directory is not None:
            await complete_on_cancel(asyncio.to_thread(directory.cleanup))


async def push_file(
    serial: str, local_path: str | os.PathLike[str] | bytes, remote_path: str,
    *, timeout: float = 60.0,
) -> bool:
    """通过 adb push 复制文件或 bytes；bytes 的临时文件在取消回收后删除。"""
    _check_transfer(remote_path, timeout)
    source = local_path if isinstance(local_path, bytes) else _local_path(local_path)
    try:
        if isinstance(source, bytes):
            async with _transfer_file() as path:
                await complete_on_cancel(asyncio.to_thread(path.write_bytes, source))
                return await adb_device_cmd(
                    serial, "push", str(path), remote_path, timeout=timeout
                )
        if not await isfile(source):
            logger.error(f"本地文件不存在：{source}")
            return False
        return await adb_device_cmd(serial, "push", source, remote_path, timeout=timeout)
    except OSError as error:
        logger.warning(f"文件推送失败：{error!r}")
        return False


async def pull_file(
    serial: str, remote_path: str, local_path: str | os.PathLike[str] | None = None,
    *, timeout: float = 60.0,
) -> bool | bytes | None:
    """通过 adb pull 写入本地路径，或从独立临时文件读取原始 bytes。"""
    _check_transfer(remote_path, timeout)
    target = None if local_path is None else _local_path(local_path)
    try:
        if target is not None:
            return await adb_device_cmd(
                serial, "pull", remote_path, target, timeout=timeout
            )
        async with _transfer_file() as path:
            if not await adb_device_cmd(
                serial, "pull", remote_path, str(path), timeout=timeout
            ):
                return None
            return await complete_on_cancel(asyncio.to_thread(path.read_bytes))
    except OSError as error:
        logger.warning(f"文件拉取失败：{error!r}")
        return None if target is None else False


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
