"""Windows Media Foundation adapter; native queues own COM and decoding work."""

import asyncio

from ...async_utils import complete_on_cancel
from .. import _adb_scr_media as media
from .decoder_base import H264DecoderBase

__all__ = []


class MfH264Decoder(H264DecoderBase):
    def __init__(self, sps_and_pps: bytes) -> None:
        super().__init__()
        self.width, self.height, self.handle = media.create_decoder(sps_and_pps)
        self.used = False
        self.valid = True

    async def close_decoder(self) -> None:
        if self.valid:
            self.valid = False
            await complete_on_cancel(asyncio.to_thread(media.destroy_decoder, self.handle))

    def enqueue_frame(self, is_idr: bool, nalu: bytes, pts: int) -> bool:
        if not self.valid:
            return False
        if not self.used and not is_idr:
            return True
        accepted = media.enqueue_frame(self.handle, nalu, pts)
        self.used = self.used or accepted
        return accepted

    async def check_error(self) -> None:
        await complete_on_cancel(asyncio.to_thread(media.check_decoder_error, self.handle))

    async def get_current_frame_bgra8(self) -> tuple[int, int, bytes] | None:
        return await complete_on_cancel(
            asyncio.to_thread(media.get_current_frame_bgra8, self.handle)
        )

    async def get_current_frame_jpg(
        self, quality: int = 75, scale: float = 1.0,
        roi: tuple[int, int, int, int] | None = None,
    ) -> bytes | None:
        return await complete_on_cancel(
            asyncio.to_thread(media.get_current_frame_jpg, self.handle, quality, scale, roi)
        )

    async def start_recording(
        self, output_file: str, audio_config: bytes | None, source_pts: int, fps: int,
        quality: float = 0.75,
    ) -> "media.RecordingHandle":
        # RecordingController protects both creation and result installation.
        return await asyncio.to_thread(
            media.start_recording, self.handle, output_file, audio_config, source_pts, fps,
            quality,
        )

    async def set_recording(self, recording: "media.RecordingHandle | None") -> None:
        await complete_on_cancel(
            asyncio.to_thread(media.set_decoder_recording, self.handle, recording)
        )
