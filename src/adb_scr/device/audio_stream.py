"""scrcpy 3.2 framed AAC input; codec work stays in the native layer."""

import asyncio
from collections.abc import Awaitable, Callable

from .bin_utils import decode_frame_header, from_u32_be

__all__ = []


class AudioStream:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        timeout: float,
        consume: Callable[[bytes, int], Awaitable[None]],
    ) -> None:
        self.reader = reader
        self.timeout = timeout
        self.consume = consume
        self.config: bytes | None = None
        self.disabled = False
        self.ready = asyncio.Event()

    async def _packet(self, first: bytes):
        header = first + await self.reader.readexactly(11)
        size = from_u32_be(header[8:])
        if not 0 < size <= 1024 * 1024:
            raise ValueError("无效的音频包长度")
        return decode_frame_header(header[:8]), await self.reader.readexactly(size)

    async def run(self) -> None:
        try:
            codec = await asyncio.wait_for(self.reader.readexactly(4), self.timeout)
            if codec == b"\0\0\0\0":
                self.disabled = True
                return
            if codec != b"\0aac":
                raise RuntimeError("音频配置失败或服务端未使用 AAC")
            while True:
                # Once configured, silence between complete packets is valid.
                first_read = self.reader.readexactly(1)
                first = (
                    await asyncio.wait_for(first_read, self.timeout)
                    if self.config is None else await first_read
                )
                header, data = await asyncio.wait_for(self._packet(first), self.timeout)
                if header.config_flag:
                    if len(data) > 64:
                        raise ValueError("无效的 AAC 配置长度")
                    if self.config is not None and self.config != data:
                        raise RuntimeError("会话内 AAC 配置发生变化")
                    self.config = data
                    self.ready.set()
                elif self.config is None:
                    raise RuntimeError("AAC 数据先于配置到达")
                else:
                    await self.consume(data, header.pts)
        finally:
            self.ready.set()
