"""Persistent media errors and cancellation ownership; no ADB or phone."""

import asyncio
import sys
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adb_scr import AndroidDevice
from adb_scr.device.control_handle import DeviceControlHandle
from adb_scr.exceptions import MediaPipelineOverloadedError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows error monitor")


def test_decoder_failure_wakes_waiter_without_new_video_packets():
    async def run():
        failure = MediaPipelineOverloadedError("synthetic decoder overflow")
        handle = DeviceControlHandle("synthetic", "00000001")
        handle.running = True
        handle.h264_decoder = SimpleNamespace(
            check_error=AsyncMock(side_effect=failure), close_decoder=AsyncMock(),
        )
        device = AndroidDevice("synthetic", "usb")
        device.control_handle = handle
        device._last_media_handle = handle
        monitor = asyncio.create_task(handle.process_media_errors())
        handle.async_tasks.append(monitor)
        with pytest.raises(MediaPipelineOverloadedError) as result:
            await asyncio.wait_for(device.wait_media_error(), 2)
        assert result.value is failure
        await asyncio.wait_for(handle.wait_disconnected(), 2)
        device.control_handle = None
        with pytest.raises(MediaPipelineOverloadedError):
            await device.get_screenshot_jpg()
        with pytest.raises(MediaPipelineOverloadedError):
            await device.wait_media_error()
    asyncio.run(run())


@pytest.mark.parametrize("from_audio", [False, True])
def test_recording_failure_stops_file_and_keeps_session(monkeypatch, from_audio):
    from adb_scr.device import recording as module
    async def run():
        failure = MediaPipelineOverloadedError("synthetic recording overflow")
        def fail(*args):
            raise failure
        monkeypatch.setattr(module, "_media", SimpleNamespace(
            check_recording_error=fail, append_recording_audio=fail, stop_recording=fail,
        ))
        handle = DeviceControlHandle("synthetic", "00000001")
        handle.running = True
        decoder = SimpleNamespace(set_recording=AsyncMock(), close_decoder=AsyncMock())
        handle.h264_decoder = decoder
        handle.recording.active = object()
        if from_audio:
            await handle.recording.append_audio(b"aac", 12345)
        else:
            await handle.recording.check_error()
        with pytest.raises(MediaPipelineOverloadedError) as result:
            await asyncio.wait_for(handle.wait_media_error(), 1)
        assert result.value is failure
        assert handle.running
        assert handle.recording.active is None
        decoder.set_recording.assert_awaited_once_with(None)
        with pytest.raises(MediaPipelineOverloadedError):
            await handle.recording.stop()
        await handle.disconnect_sockets()
    asyncio.run(run())


def test_cancelled_error_wait_does_not_stop_session():
    async def run():
        handle = DeviceControlHandle("synthetic", "00000001")
        handle.running = True
        waiter = asyncio.create_task(handle.wait_media_error())
        await asyncio.sleep(0)
        waiter.cancel()
        with suppress(asyncio.CancelledError):
            await waiter
        assert handle.running
        assert handle._close_task is None
        await handle.disconnect_sockets()
        assert await handle.wait_media_error() is None
    asyncio.run(run())
