"""Recording orchestration with controlled native boundaries; no ADB."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from adb_scr import AndroidDevice
from adb_scr.device.control_handle import DeviceControlHandle
from adb_scr.media_ext.h264 import vtb_decoder


def install_native(monkeypatch):
    from adb_scr.device import recording

    calls = []
    clock = [1_000_000]

    def create(decoder, path, config, origin, fps, quality):
        result = object()
        calls.append(("start", result, path, config, origin, fps, quality))
        return result

    def attach(decoder, recorder):
        calls.append(("attach", decoder, recorder))

    def audio(recorder, data, pts):
        calls.append(("audio", recorder, data, pts))

    def stop(recorder, pts):
        calls.append(("stop", recorder, pts))

    media = SimpleNamespace(start_recording=create, set_decoder_recording=attach,
                            append_recording_audio=audio, stop_recording=stop)
    monkeypatch.setattr(recording, "_media", media)
    monkeypatch.setattr(vtb_decoder, "_media", media, raising=False)
    monkeypatch.setattr(recording, "monotonic_us", lambda: clock[0])
    return media, calls, clock


def decoder():
    result = object.__new__(vtb_decoder.VtbH264Decoder)
    result.valid = True
    result.handle = object()
    result.width = result.height = 64
    return result


def handle_with_frame():
    handle = DeviceControlHandle("synthetic", "00000001")
    handle.running = True
    handle.h264_decoder = decoder()
    return handle


@pytest.mark.parametrize("options, expected", [({}, 0.75), ({"quality": 0.4}, 0.4),
                                               ({"quality": 0}, 0.0), ({"quality": 1}, 1.0)])
def test_public_recording_quality_reaches_native_encoder(monkeypatch, options, expected):
    async def run():
        _, calls, _ = install_native(monkeypatch)
        handle = handle_with_frame()
        handle.recording.observe_pts(0)
        device = AndroidDevice("synthetic", "usb")
        device.control_handle = handle
        device.scrcpy_server_process = SimpleNamespace(returncode=None)
        await device.start_recording("quality.mp4", **options)
        assert calls[0][-1] == expected
        await device.stop_recording()

    asyncio.run(run())


@pytest.mark.parametrize("quality, error", [
    (-0.01, ValueError), (1.01, ValueError), (float("nan"), ValueError),
    (float("inf"), ValueError), (float("-inf"), ValueError),
    (True, TypeError), (False, TypeError), ("0.75", TypeError), (None, TypeError),
])
def test_invalid_recording_quality_is_rejected_before_native_work(monkeypatch, quality, error):
    async def run():
        _, calls, _ = install_native(monkeypatch)
        handle = handle_with_frame()
        with pytest.raises(error, match="quality"):
            await handle.recording.start("invalid.mp4", quality=quality)
        assert not calls

    asyncio.run(run())


def test_start_uses_current_clock_not_old_static_frame_and_stop_detaches(monkeypatch):
    async def run():
        _, calls, clock = install_native(monkeypatch)
        handle = handle_with_frame()
        handle.recording.observe_pts(5_000_000)
        clock[0] += 10_000_000
        await handle.recording.start("static.mp4")
        assert calls[0][4] == 15_000_000
        recorder = calls[0][1]
        clock[0] += 2_000_000
        await handle.recording.stop()
        await handle.recording.stop()
        assert calls[-2:] == [("attach", handle.h264_decoder.handle, None),
                               ("stop", recorder, 17_000_000)]

    asyncio.run(run())


def test_audio_is_drained_before_recording_and_passed_through_while_active(monkeypatch):
    async def run():
        _, calls, clock = install_native(monkeypatch)
        handle = handle_with_frame()
        handle.audio_stream = SimpleNamespace(config=b"\x11\x90", disabled=False)
        await handle.recording.append_audio(b"before", 5_000_000)
        assert not calls
        await handle.recording.start("audio.mp4")
        assert calls[0][3] == b"\x11\x90"
        await handle.recording.append_audio(b"unchanged AAC", 5_021_333)
        assert calls[-1][2:] == (b"unchanged AAC", 5_021_333)
        clock[0] += 100_000
        await handle.recording.stop()
        count = len(calls)
        await handle.recording.append_audio(b"after", 5_100_000)
        assert len(calls) == count

    asyncio.run(run())


def test_duplicate_start_does_not_replace_file_or_recorder(monkeypatch):
    async def run():
        _, calls, _ = install_native(monkeypatch)
        handle = handle_with_frame()
        handle.recording.observe_pts(0)
        await handle.recording.start("first.mp4")
        with pytest.raises(RuntimeError):
            await handle.recording.start("second.mp4")
        assert len([c for c in calls if c[0] == "start"]) == 1
        await handle.recording.stop()
        await handle.recording.start("second.mp4")
        await handle.recording.stop()
        assert [c[2] for c in calls if c[0] == "start"] == ["first.mp4", "second.mp4"]

    asyncio.run(run())


def test_start_and_stop_are_available_and_disconnected_start_fails():
    async def run():
        device = AndroidDevice("synthetic", "usb")
        assert hasattr(device, "start_recording"), "public recording API missing"
        with pytest.raises(RuntimeError):
            await device.start_recording("absent.mp4")
        await device.stop_recording()

    asyncio.run(run())


@pytest.mark.parametrize("path", [None, 123, "", "bad\0path"])
def test_invalid_path_is_rejected_before_native_work(monkeypatch, path):
    async def run():
        _, calls, _ = install_native(monkeypatch)
        handle = handle_with_frame()
        with pytest.raises((TypeError, ValueError)):
            await handle.recording.start(path)
        assert not calls

    asyncio.run(run())


def test_cancellation_during_creation_finishes_and_releases_owned_recording(monkeypatch):
    async def run():
        media, calls, _ = install_native(monkeypatch)
        entered, release = threading.Event(), threading.Event()
        create = media.start_recording

        def blocked(*args):
            entered.set()
            assert release.wait(2)
            return create(*args)

        media.start_recording = blocked
        handle = handle_with_frame()
        handle.recording.observe_pts(0)
        task = asyncio.create_task(handle.recording.start("cancelled.mp4"))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert handle.recording.active is None
        assert [c[0] for c in calls] == ["start", "attach", "stop"]

    asyncio.run(run())


def test_cancellation_during_stop_waits_for_mp4_completion(monkeypatch):
    async def run():
        media, calls, _ = install_native(monkeypatch)
        entered, release = threading.Event(), threading.Event()
        stop = media.stop_recording

        def blocked(*args):
            entered.set()
            assert release.wait(2)
            stop(*args)

        media.stop_recording = blocked
        handle = handle_with_frame()
        handle.recording.observe_pts(0)
        await handle.recording.start("stop.mp4")
        task = asyncio.create_task(handle.recording.stop())
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls[-1][0] == "stop"
        await handle.recording.stop()

    asyncio.run(run())


def test_cancelled_stop_waiting_for_audio_worker_still_finalizes(monkeypatch):
    async def run():
        media, calls, _ = install_native(monkeypatch)
        entered, release = threading.Event(), threading.Event()

        def blocked(*args):
            entered.set()
            assert release.wait(2)

        media.append_recording_audio = blocked
        handle = handle_with_frame()
        handle.recording.observe_pts(0)
        await handle.recording.start("queued-stop.mp4")
        audio = asyncio.create_task(handle.recording.append_audio(b"AAC", 20000))
        task = None
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task = asyncio.create_task(handle.recording.stop())
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done(), "cancelled stop abandoned its pending cleanup"
        finally:
            release.set()
            await audio
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        assert calls[-1][0] == "stop"
        assert handle.recording.active is None

    asyncio.run(run())


def test_audio_keeps_draining_while_stopped_file_is_finishing(monkeypatch):
    async def run():
        media, calls, _ = install_native(monkeypatch)
        entered, release = threading.Event(), threading.Event()
        finish = media.stop_recording

        def blocked(*args):
            entered.set()
            assert release.wait(2)
            finish(*args)

        media.stop_recording = blocked
        handle = handle_with_frame()
        handle.recording.observe_pts(0)
        await handle.recording.start("slow-finish.mp4")
        task = asyncio.create_task(handle.recording.stop())
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            await asyncio.wait_for(handle.recording.append_audio(b"discarded", 50000), 0.1)
            assert not any(call[0] == "audio" for call in calls)
        finally:
            release.set()
            await task

    asyncio.run(run())


def test_disconnect_finishes_recording_before_decoder_close(monkeypatch):
    async def run():
        _, calls, clock = install_native(monkeypatch)
        handle = handle_with_frame()
        handle.recording.observe_pts(0)

        async def close():
            calls.append(("decoder-close",))

        handle.h264_decoder.close_decoder = close
        await handle.recording.start("disconnect.mp4")
        clock[0] += 100_000
        await handle.disconnect_sockets()
        assert [c[0] for c in calls][-3:] == ["attach", "stop", "decoder-close"]
        assert handle.recording.active is None

    asyncio.run(run())


def test_decoder_replacement_keeps_recorder_and_reattaches(monkeypatch):
    from adb_scr.device import control_handle as module

    async def run():
        _, calls, _ = install_native(monkeypatch)
        handle = handle_with_frame()
        old = handle.h264_decoder
        new = decoder()
        handle.recording.observe_pts(0)

        async def close():
            calls.append(("decoder-close",))

        old.close_decoder = close
        monkeypatch.setattr(module, "create_h264_decoder", lambda data: new)
        await handle.recording.start("rotate.mp4")
        recorder = handle.recording.active
        async with handle._mutex:
            await handle._replace_decoder(b"config")
        assert calls[-3:] == [("attach", old.handle, None), ("decoder-close",),
                               ("attach", new.handle, recorder)]
        await handle.recording.stop()

    asyncio.run(run())


def test_writer_error_remains_visible_after_disconnect(monkeypatch):
    async def run():
        media, _, _ = install_native(monkeypatch)
        handle = handle_with_frame()
        handle.recording.observe_pts(0)
        device = AndroidDevice("synthetic", "usb")
        device.control_handle = handle
        device.scrcpy_server_process = SimpleNamespace(returncode=None)

        async def close():
            pass

        handle.h264_decoder.close_decoder = close
        await device.start_recording("failure.mp4")

        def fail(*args):
            raise OSError("disk full")

        media.stop_recording = fail
        await handle.disconnect_sockets()
        device.control_handle = None
        with pytest.raises(OSError, match="disk full"):
            await device.stop_recording()

    asyncio.run(run())
