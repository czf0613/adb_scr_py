"""通过 ADB daemon 建立到手机 abstract Unix socket 的流。"""

import asyncio

from ..async_utils import close_writer, complete_on_cancel
from ..logger import logger

__all__ = []


async def _push_cmd(writer: asyncio.StreamWriter, cmd: str) -> None:
    payload = cmd.encode("utf-8")
    writer.write(f"{len(payload):04x}".encode("ascii") + payload)
    await writer.drain()


async def _receive_cmd_resp(reader: asyncio.StreamReader) -> None | str:
    """读取 ADB 状态；None 表示成功，否则返回错误说明。"""
    status = await reader.readexactly(4)
    if status == b"OKAY":
        return None
    if status == b"FAIL":
        size = int(await reader.readexactly(4), 16)
        return (await reader.readexactly(size)).decode("utf-8", errors="replace")
    return f"未知 ADB 状态：{status!r}"


async def setup_tunnel(
    serial: str, uds: str, *, timeout: float = 5.0, close_timeout: float = 5.0
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    """在总超时内建连、选择设备并连接 UDS；失败时回收已打开的流。"""
    writer = None
    transferred = False

    async def handshake():
        nonlocal writer
        reader, writer = await asyncio.open_connection("localhost", 5037)
        for command in (f"host:transport:{serial}", uds):
            await _push_cmd(writer, command)
            error = await _receive_cmd_resp(reader)
            if error is not None:
                raise ConnectionError(error)
        return reader, writer

    try:
        result = await asyncio.wait_for(handshake(), timeout)
        transferred = True
        return result
    except Exception as error:
        logger.warning(f"连接设备 {serial} 失败：{error!r}")
        return None
    finally:
        if writer is not None and not transferred:
            try:
                await complete_on_cancel(close_writer(writer, close_timeout))
            except Exception as error:
                logger.warning(f"关闭失败隧道：{error!r}")
