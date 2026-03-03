import asyncio
from ..logger import logger

__all__ = []


async def _push_cmd(writer: asyncio.StreamWriter, cmd: str) -> None:
    text_fmt = f"{len(cmd):04x}{cmd}"
    data = text_fmt.encode("utf-8")

    writer.write(data)
    await writer.drain()


async def _receive_cmd_resp(reader: asyncio.StreamReader) -> None | str:
    """
    接收adb命令的响应。注意！返回None表示成功，否则返回错误信息。
    """
    try:
        resp_status = await reader.readexactly(4)
        if resp_status == b"OKAY":
            # 正常返回响应
            return None
        elif resp_status == b"FAIL":
            # 失败返回错误信息
            resp_len_data = await reader.readexactly(4)
            resp_len_hex = resp_len_data.decode("utf-8")
            resp_len = int(resp_len_hex, base=16)
            resp = await reader.readexactly(resp_len)
            return resp.decode("utf-8")
        else:
            # 其他状态码，返回错误信息
            return f"未知状态码：{resp_status.decode('utf-8')}"
    except Exception as e:
        logger.error(f"接收adb命令响应失败：{e}")
        return f"接收adb命令响应失败：{e}"


async def setup_tunnel(
    serial: str, uds: str
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    """
    通过模拟adb协议，把socket数据映射到手机的uds上面
    """
    try:
        tcp_reader, tcp_writer = await asyncio.wait_for(
            asyncio.open_connection("localhost", 5037), timeout=5
        )

        # 切换到指定设备
        await _push_cmd(tcp_writer, f"host:transport:{serial}")
        err = await _receive_cmd_resp(tcp_reader)
        if err is not None:
            logger.error(f"切换到设备{serial}失败：{err}")
            tcp_writer.close()
            return None

        # 直接读取手机的uds
        await _push_cmd(tcp_writer, uds)
        err = await _receive_cmd_resp(tcp_reader)
        if err is not None:
            logger.error(f"读取手机uds{uds}失败：{err}")
            tcp_writer.close()
            return None

        # 读写这个socket就相当于操作uds了
        return tcp_reader, tcp_writer
    except Exception as e:
        logger.error(f"连接设备{serial}失败：{e}")
        return None
