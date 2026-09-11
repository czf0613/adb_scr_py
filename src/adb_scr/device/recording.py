"""Per-session recording ownership and device/host clock mapping."""

import asyncio
import time
from typing import TYPE_CHECKING

from .. import consts
from ..async_utils import complete_on_cancel
from ..logger import logger
from ..media_ext import _adb_scr_media as _media

if TYPE_CHECKING:
    from .control_handle import DeviceControlHandle
    from ..media_ext._adb_scr_media import RecordingHandle

__all__ = []


def monotonic_us() -> int:
    return time.monotonic_ns() // 1000


class RecordingController:
    def __init__(self, owner: "DeviceControlHandle") -> None:
        self.owner = owner
        self.active: "RecordingHandle | None" = None
        self.error: Exception | None = None
        self._mutex = asyncio.Lock()
        self._clock_offset: int | None = None
        self._source_origin = 0
        self._host_origin = 0
        self._end_pts: int | None = None

    def observe_pts(self, pts: int) -> None:
        # A lower observed transit delay gives a better shared clock estimate.
        # Both media streams use the same mapping; recording freezes its origin.
        offset = monotonic_us() - pts
        if self._clock_offset is None or offset < self._clock_offset:
            self._clock_offset = offset

    def freeze_end(self) -> None:
        if self.active is not None and self._end_pts is None:
            self._end_pts = self._source_origin + max(1, monotonic_us() - self._host_origin)

    def fail(self, error: Exception) -> None:
        if self.active is not None and self.error is None:
            self.error = error
        self.freeze_end()

    async def start(self, output_file: str) -> None:
        if not isinstance(output_file, str):
            raise TypeError("output_file 必须为字符串")
        if not output_file or "\0" in output_file:
            raise ValueError("output_file 不能为空或包含 NUL")
        async with self._mutex:
            if self.active is not None:
                raise RuntimeError("本设备已有正在进行的录制")
            self.error = None
            try:
                await complete_on_cancel(self._start(output_file))
            except BaseException:
                # Creation includes installing its result. A cancelled worker
                # may already own a file/session; finish it before returning.
                if self.active is not None:
                    try:
                        await complete_on_cancel(self._finish())
                    except Exception as error:
                        logger.warning(f"回收未完成的录制启动失败：{error!r}")
                raise

    async def _start(self, output_file: str) -> None:
        async with self.owner._mutex:
            decoder = self.owner.h264_decoder
            if not self.owner.running or decoder is None or self._clock_offset is None:
                raise RuntimeError("设备未连接或尚无已解码画面")
            stream = self.owner.audio_stream
            config = stream.config if stream is not None and not stream.disabled else None
            if config is None:
                logger.warning("本会话音频不可用，录制仅包含视频")
            self._host_origin = monotonic_us()
            self._source_origin = self._host_origin - self._clock_offset
            self._end_pts = None
            self.active = await decoder.start_recording(
                output_file, config, self._source_origin, consts.SCREEN_FPS
            )
            if not self.owner.running:
                raise RuntimeError("开始录制时设备已断开")

    async def append_audio(self, data: bytes, pts: int) -> None:
        self.observe_pts(pts)
        # Once stop is signalled, keep draining the live socket while the file
        # finishes. Starting a new recording resets this before native creation.
        if self._end_pts is not None:
            return
        async with self._mutex:
            if self.active is None or self._end_pts is not None:
                return
            try:
                await complete_on_cancel(
                    asyncio.to_thread(_media.append_recording_audio, self.active, data, pts)
                )
            except Exception as error:
                self.fail(error)
                raise

    async def _finish(self) -> None:
        if self.active is None:
            if self.error is not None:
                raise self.error
            return
        self.freeze_end()
        recorder = self.active
        try:
            async with self.owner._mutex:
                if self.owner.h264_decoder is not None:
                    await self.owner.h264_decoder.set_recording(None)
        except Exception as error:
            self.fail(error)
        finally:
            self.active = None
            try:
                await asyncio.to_thread(_media.stop_recording, recorder, self._end_pts)
            except Exception as error:
                if self.error is None:
                    self.error = error
        if self.error is not None:
            raise self.error

    async def stop(self) -> None:
        self.freeze_end()
        await complete_on_cancel(self._stop_serialized())

    async def _stop_serialized(self) -> None:
        async with self._mutex:
            await self._finish()
