from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

__all__ = []


class H264DecoderBase(ABC):
    """内部解码器接口。

    Notes:
        调用方必须协调入队、取帧、替换和关闭的生命周期。控制句柄通过
        asyncio.Lock 提供同一事件循环内的保护；不要跨事件循环共享实例。
    """

    if TYPE_CHECKING:
        # 该解码器是否还有效
        valid: bool
        width: int
        height: int

    def __init__(self) -> None:
        """子类实现这个方法时需要顺带初始化内部的解码器句柄，所以这个方法可能会抛出异常（类里面的其它方法不会）

        Raises:
            AdbScrPyH264DecoderException: 如果初始化失败，则抛出此异常
        """
        self.valid = False
        self.width = 0
        self.height = 0

    @abstractmethod
    async def close_decoder(self) -> None:
        """
        关闭解码器句柄
        """
        pass

    @abstractmethod
    def enqueue_frame(self, is_idr: bool, nalu: bytes, pts: int) -> bool:
        """快速提交一帧 H.264 数据供异步解码。

        Args:
            is_idr: 是否为 IDR 关键帧。
            nalu: 以 Annex B 起始码分隔的 H.264 数据。
            pts: 显示时间戳，单位为微秒。

        Returns:
            成功接收或因首个 IDR 尚未到达而跳过时为 True，失败时为 False。
            True 不表示解码已完成。
        """
        pass

    @abstractmethod
    async def get_current_frame_bgra8(self) -> tuple[int, int, bytes] | None:
        """获取当前视频帧的BGRA8数据。

        Returns:
            如果获取成功，则返回一个元组，包含视频的宽度、高度和BGRA8格式的视频帧数据；否则返回None
        """
        pass
