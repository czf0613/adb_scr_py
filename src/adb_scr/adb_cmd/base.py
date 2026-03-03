import asyncio
from asyncio import subprocess
from ..logger import logger
from .. import consts

__all__ = []


async def adb_version() -> str:
    """
    获取ADB版本号。
    :return: ADB版本号字符串。
    """
    process = await subprocess.create_subprocess_exec(
        consts.ADB_PATH,
        "version",
        limit=2048,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logger.error(f"获取ADB版本失败：{stderr.decode()}")
        return "unknown"

    adb_version_output = stdout.decode().split("\n")
    adb_version_line = next(
        filter(
            lambda line: line.startswith("Android Debug Bridge version"),
            adb_version_output,
        ),
        None,
    )

    if adb_version_line is not None:
        return adb_version_line.replace("Android Debug Bridge version", "").strip()
    else:
        logger.error(f"获取ADB版本失败：{adb_version_output}")
        return "unknown"


async def start_adb_daemon() -> int:
    """
    启动ADB守护进程。
    :return: 进程状态码。0表示成功。
    """
    process = await subprocess.create_subprocess_exec(
        consts.ADB_PATH,
        "start-server",
        stdin=subprocess.DEVNULL,
    )

    return await process.wait()


async def kill_adb_daemon() -> None:
    """
    终止ADB守护进程。
    默认执行成功，不会抛出异常。
    """
    process = await subprocess.create_subprocess_exec(
        consts.ADB_PATH,
        "kill-server",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    await process.wait()


async def adb_connect(device_addr: str, timeout: float = 10.0) -> int:
    """
    连接到ADB设备（仅限网络设备）。
    :param timeout: 连接超时时间，单位秒。
    :return: 进程状态码。0表示成功。
    """
    process = await subprocess.create_subprocess_exec(
        consts.ADB_PATH,
        "connect",
        device_addr,
        stdin=subprocess.DEVNULL,
    )

    try:
        return await asyncio.wait_for(process.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        # 超时时杀死进程
        process.kill()
        return -1


async def adb_disconnect(device_addr: str) -> None:
    """
    断开ADB网络设备连接。
    默认执行成功，不会抛出异常。
    """
    process = await subprocess.create_subprocess_exec(
        consts.ADB_PATH,
        "disconnect",
        device_addr,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    await process.wait()


async def adb_devices() -> list[str]:
    """
    获取当前连接的ADB设备列表。
    :return: 设备列表，每个元素为设备地址/序列号。
    """
    process = await subprocess.create_subprocess_exec(
        consts.ADB_PATH,
        "devices",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        logger.error(f"获取ADB设备列表失败：{stderr.decode()}")
        return []

    # 这是个tsv，第一行是表头，不包含设备信息，第一列的是设备地址/序列号
    devices_output = list(
        filter(
            lambda x: len(x) > 0,
            map(lambda l: l.strip(), stdout.decode().split("\n")[1:]),
        )
    )

    if len(devices_output) == 0:
        logger.warning("无连接的ADB设备")
        return []

    devices = list(
        filter(
            lambda x: len(x) > 0,
            map(lambda l: l.split("\t")[0].strip(), devices_output),
        )
    )
    return devices


async def adb_device_cmd(serial: str, cmd: str, *args: str) -> bool:
    """
    针对指定执行ADB设备命令。相当于`adb -s SERIAL xxx xxxx`
    """
    process = await subprocess.create_subprocess_exec(
        consts.ADB_PATH,
        "-s",
        serial,
        cmd,
        *args,
        stdin=subprocess.DEVNULL,
    )

    return await process.wait() == 0
