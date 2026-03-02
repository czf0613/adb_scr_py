from .decoder_base import H264DecoderBase
from adb_scr.exceptions import AdbScrPyH264DecoderException
from typing import TYPE_CHECKING, final
from .._adb_scr_media import (
    create_decoder,
    destroy_decoder,
    enqueue_frame,
    get_current_frame_bgra8,
)
import asyncio
from adb_scr.logger import logger


@final
class VtbH264Decoder(H264DecoderBase):
    if TYPE_CHECKING:
        from .._adb_scr_media import DecoderHandle

        handle: DecoderHandle
        # 是否已被使用过，vtb解码器比较脆弱，如果输入进去的第一帧不是IDR会崩溃，但是应用层无法保证这个操作，得自己判断
        used: bool

    def __init__(self, sps_and_pps: bytes) -> None:
        """
        调用VideoToolbox的编码器初始化解码器
        :param sps_and_pps: SPS和PPS参数，用于初始化解码器(0x00 0x00 0x00 0x01开头的ANNEXB格式)
        :raises AdbScrPyH264DecoderException: 如果初始化失败，则抛出此异常
        """
        super().__init__()
        self.used = False

        creation_result = create_decoder(sps_and_pps)
        if creation_result is None:
            raise AdbScrPyH264DecoderException("创建解码器失败")

        self.width, self.height, self.handle = creation_result
        self.valid = True

    async def close_decoder(self) -> None:
        if not self.valid:
            return

        self.valid = False
        await asyncio.to_thread(destroy_decoder, self.handle)

    def enqueue_frame(self, is_idr: bool, nalu: bytes, pts: int) -> bool:
        if not self.valid:
            logger.warning("解码器句柄已关闭，无法入队帧")
            return False

        if not self.used and not is_idr:
            # 还未使用过，且输入的这个不是IDR，那就静默吞掉
            return True

        if enqueue_frame(self.handle, nalu, pts):
            self.used = True
            return True
        else:
            logger.warning("入队帧失败")
            return False

    async def get_current_frame_bgra8(self) -> tuple[int, int, bytes] | None:
        if not self.valid:
            logger.warning("解码器句柄已关闭，无法获取当前视频帧")
            return None

        return await asyncio.to_thread(get_current_frame_bgra8, self.handle)
