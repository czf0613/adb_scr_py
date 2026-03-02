from abc import ABC, abstractmethod
from typing import TYPE_CHECKING


class H264DecoderBase(ABC):
    """这个类没有保证线程安全，所以调用方要自己想点办法保证线程安全，防止并发修改state导致崩溃"""

    if TYPE_CHECKING:
        # 该解码器是否还有效
        valid: bool
        width: int
        height: int

    def __init__(self) -> None:
        """子类实现这个方法时需要顺带初始化内部的解码器句柄，所以这个方法可能会抛出异常（类里面的其它方法不会）
        :raises AdbScrPyH264DecoderException: 如果初始化失败，则抛出此异常
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
        """
        向解码器句柄队列中入队一帧NALU数据。这个方法严禁阻塞，比如立刻丢入队列中然后迅速返回。
        :param is_idr: 是否为IDR帧
        :param nalu: NALU数据(0x00 0x00 0x00 0x01开头的格式)
        :param pts: 帧的PTS值，看具体解码器的约定
        :return: 如果入队成功，则返回True；否则返回False
        """
        pass

    @abstractmethod
    async def get_current_frame_bgra8(self) -> tuple[int, int, bytes] | None:
        """
        获取当前视频帧的BGRA8数据。
        :return: 如果获取成功，则返回一个元组，包含视频的宽度、高度和BGRA8格式的视频帧数据；否则返回None
        """
        pass
