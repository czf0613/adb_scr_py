import asyncio
import random
from typing import TYPE_CHECKING, final

__all__ = []


def to_u32_be(value: int) -> bytes:
    return value.to_bytes(4, byteorder="big", signed=False)


def to_u16_be(value: int) -> bytes:
    return value.to_bytes(2, byteorder="big", signed=False)


def from_u64_be(data: bytes) -> int:
    assert len(data) == 8
    return int.from_bytes(data, byteorder="big", signed=False)


def from_u32_be(data: bytes) -> int:
    assert len(data) == 4
    return int.from_bytes(data, byteorder="big", signed=False)


def from_u16_be(data: bytes) -> int:
    assert len(data) == 2
    return int.from_bytes(data, byteorder="big", signed=False)


async def random_sleep_ms(min_ms: int, max_ms: int) -> None:
    """随机等待一段时间（随机范围内）

    Args:
        min_ms: 最小等待时间（毫秒）
        max_ms: 最大等待时间（毫秒）
    """
    assert min_ms >= 0 and max_ms >= 0 and min_ms <= max_ms, "时间范围异常"
    rand_ms = random.randint(min_ms, max_ms)
    await asyncio.sleep(float(rand_ms) / 1000)


def decode_lossy_utf8(data: bytes) -> str:
    """移除尾部 NUL 填充，再严格解码 UTF-8。

    Raises:
        UnicodeDecodeError: 有效内容含非法 UTF-8 字节。
    """
    return data.rstrip(b"\0").decode("utf-8")


@final
class FrameHeader:
    if TYPE_CHECKING:
        config_flag: bool
        key_flag: bool
        # 单位是微秒（1秒 = 1000000微秒）
        pts: int

    def __init__(self, uint64: int) -> None:
        self.config_flag = ((uint64 >> 63) & 1) == 1
        self.key_flag = ((uint64 >> 62) & 1) == 1
        self.pts = uint64 & 0x3FFFFFFFFFFFFFFF


def decode_frame_header(data: bytes) -> FrameHeader:
    u64 = from_u64_be(data)
    return FrameHeader(u64)
