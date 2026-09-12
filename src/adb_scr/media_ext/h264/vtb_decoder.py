import asyncio
from typing import TYPE_CHECKING, final

from adb_scr.exceptions import AdbScrPyH264DecoderException
from adb_scr.logger import logger

from ...async_utils import complete_on_cancel
from .. import _adb_scr_media as _media
from .._adb_scr_media import (
    create_decoder,
    destroy_decoder,
    enqueue_frame,
    get_current_frame_bgra8,
    get_current_frame_jpg,
)
from .decoder_base import H264DecoderBase

if TYPE_CHECKING:
    from .._adb_scr_media import RecordingHandle

__all__ = []


@final
class VtbH264Decoder(H264DecoderBase):
    if TYPE_CHECKING:
        from .._adb_scr_media import DecoderHandle

        handle: DecoderHandle
        # 是否已被使用过，vtb解码器比较脆弱，如果输入进去的第一帧不是IDR会崩溃，但是应用层无法保证这个操作，得自己判断
        used: bool

    def __init__(self, sps_and_pps: bytes) -> None:
        """根据 SPS/PPS 创建 VideoToolbox 硬件解码器。

        Args:
            sps_and_pps: Annex B 格式的 SPS 和 PPS 数据。

        Raises:
            AdbScrPyH264DecoderException: 参数解析或硬件解码器创建失败。

        Notes:
            构造包含系统调用，应通过 asyncio.to_thread() 从异步路径调用。
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
        await complete_on_cancel(asyncio.to_thread(destroy_decoder, self.handle))

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

        return await complete_on_cancel(
            asyncio.to_thread(get_current_frame_bgra8, self.handle)
        )

    async def get_current_frame_jpg(
        self,
        quality: int = 75,
        scale: float = 1.0,
        roi: tuple[int, int, int, int] | None = None,
    ) -> bytes | None:
        if not self.valid:
            return None
        return await complete_on_cancel(
            asyncio.to_thread(get_current_frame_jpg, self.handle, quality, scale, roi)
        )

    async def start_recording(
        self, output_file: str, audio_config: bytes | None, source_pts: int, fps: int,
        quality: float = 0.75,
    ) -> "RecordingHandle":
        if not self.valid:
            raise RuntimeError("解码器已关闭")
        # The controller shields result installation, not only this worker.
        return await asyncio.to_thread(
            _media.start_recording, self.handle, output_file, audio_config, source_pts, fps,
            quality,
        )

    async def set_recording(self, recording: "RecordingHandle | None") -> None:
        await complete_on_cancel(
            asyncio.to_thread(_media.set_decoder_recording, self.handle, recording)
        )
