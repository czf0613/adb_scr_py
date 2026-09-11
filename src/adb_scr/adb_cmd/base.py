"""有超时及取消清理的 ADB 子进程操作。"""

import asyncio
from asyncio import subprocess

from .. import consts
from ..async_utils import complete_on_cancel, stop_process
from ..logger import logger

__all__ = []


async def _run(
    *args: str, timeout: float = 10.0, capture: bool = False
) -> tuple[int, bytes, bytes]:
    """运行 ADB 命令；超时或取消时终止并回收本次子进程。"""
    process = None

    async def spawn():
        nonlocal process
        process = await subprocess.create_subprocess_exec(
            consts.ADB_PATH,
            *args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
        )

    async def execute():
        await complete_on_cancel(spawn())
        stdout, stderr = await process.communicate()
        return process.returncode, stdout or b"", stderr or b""

    try:
        return await asyncio.wait_for(execute(), timeout)
    finally:
        if process is not None:
            await complete_on_cancel(stop_process(process))


async def adb_version() -> str:
    """返回 ADB 版本；命令失败时返回 unknown。"""
    code, stdout, _ = await _run("version", capture=True)
    prefix = "Android Debug Bridge version"
    if code == 0:
        for line in stdout.decode(errors="replace").splitlines():
            if line.startswith(prefix):
                return line.removeprefix(prefix).strip()
    return "unknown"


async def start_adb_daemon() -> int:
    """启动全局 ADB daemon，返回退出码。"""
    code, _, _ = await _run("start-server")
    return code


async def kill_adb_daemon() -> None:
    """停止全局 ADB daemon；超时或启动命令失败会抛出异常。"""
    await _run("kill-server")


async def adb_connect(device_addr: str, timeout: float = 10.0) -> int:
    """连接网络设备，返回退出码；超时或命令无法启动时返回 -1。"""
    try:
        code, _, _ = await _run("connect", device_addr, timeout=timeout)
        return code
    except (OSError, asyncio.TimeoutError):
        return -1


async def adb_disconnect(device_addr: str, timeout: float = 10.0) -> None:
    """断开 ADB 网络 transport；异常由调用方处理。"""
    await _run("disconnect", device_addr, timeout=timeout)


async def adb_devices() -> list[str]:
    """返回 adb devices 输出中的序列号，可能包含 offline/unauthorized 设备。"""
    code, stdout, _ = await _run("devices", capture=True)
    if code != 0:
        return []
    return [
        line.split()[0]
        for line in stdout.decode(errors="replace").splitlines()[1:]
        if line.strip()
    ]


async def adb_android_api_level(serial: str, timeout: float = 10.0) -> int:
    """在启动 scrcpy 前读取设备 API 级别；失败或非法结果不猜测版本。"""
    code, stdout, _ = await _run(
        "-s", serial, "shell", "getprop", "ro.build.version.sdk",
        capture=True, timeout=timeout,
    )
    value = stdout.strip()
    if code != 0 or not value.isdigit() or not 0 < int(value) < 10000:
        raise RuntimeError("无法读取有效的 Android API 级别")
    return int(value)


async def adb_device_cmd(
    serial: str, cmd: str, *args: str, timeout: float = 10.0
) -> bool:
    """运行指定设备命令，成功返回 True；超时或启动失败返回 False。"""
    try:
        code, _, _ = await _run("-s", serial, cmd, *args, timeout=timeout)
        return code == 0
    except (OSError, asyncio.TimeoutError) as error:
        logger.warning(f"ADB 设备命令失败：{error!r}")
        return False
