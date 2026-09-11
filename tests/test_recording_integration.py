"""Public recording API through real framed streams and native media, without ADB."""

import asyncio
import re
import subprocess
from types import SimpleNamespace

import pytest

from adb_scr import AndroidDevice, ConnectionOptions
from adb_scr.device import control_handle, recording
from test_recording_native import probe, synthetic as synthetic


def media_packet(payload, pts=0, *, config=False, key=False):
    stamp = (1 << 63) if config else pts | ((1 << 62) if key else 0)
    return stamp.to_bytes(8, "big") + len(payload).to_bytes(4, "big") + payload


async def connected_device(monkeypatch, synthetic, *, audio):
    readers = []
    config, frame = synthetic[0]["red"]

    class Writer:
        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def tunnel(*args, **kwargs):
        reader = asyncio.StreamReader()
        index = len(readers)
        readers.append(reader)
        if index == 0:
            reader.feed_data(
                b"\0" + b"synthetic".ljust(64, b"\0") + b"h264"
                + (64).to_bytes(4, "big") * 2
                + media_packet(config, config=True)
                + media_packet(frame, 10_000_000, key=True)
            )
        elif index == 1 and audio:
            reader.feed_data(b"\0aac" + media_packet(synthetic[1], config=True))
        return reader, Writer()

    monkeypatch.setattr(control_handle, "setup_tunnel", tunnel)
    handle = control_handle.DeviceControlHandle(
        "synthetic", "00000001", audio_enabled=audio,
        options=ConnectionOptions(io_timeout=1, probe_interval=None),
    )
    device = AndroidDevice("synthetic", "usb")
    device.control_handle = handle
    device.scrcpy_server_process = SimpleNamespace(returncode=None)
    assert await handle.connect_sockets()

    async def first_frame():
        while await handle.get_current_frame() is None:
            await asyncio.sleep(0.001)

    await asyncio.wait_for(first_frame(), 3)
    return device, handle, readers


def test_recording_starts_from_decoded_p_frame(monkeypatch, synthetic, tmp_path):
    path = tmp_path / "interframes.h264"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10",
        "-frames:v", "8", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-threads", "1", "-x264-params", "keyint=100:min-keyint=100:scenecut=0:bframes=0",
        "-f", "h264", str(path),
    ], check=True)
    units = [unit for unit in re.split(b"\x00\x00\x00?\x01", path.read_bytes()) if unit]
    config = b"".join(b"\0\0\0\1" + unit for unit in units if unit[0] & 31 in (7, 8))
    frames = [b"\0\0\0\1" + unit for unit in units if unit[0] & 31 in (1, 5)]
    assert len(frames) == 8 and all(frame[4] & 31 == 1 for frame in frames[1:])

    async def run():
        clock = [1_000_000]
        monkeypatch.setattr(recording, "monotonic_us", lambda: clock[0])
        fixture = ({"red": (config, frames[0])}, synthetic[1], synthetic[2])
        device, handle, readers = await connected_device(monkeypatch, fixture, audio=False)
        output = tmp_path / "p-frame-start.mp4"

        async def next_frame(payload, pts):
            before = await handle.get_current_frame()
            readers[0].feed_data(media_packet(payload, pts))
            async def changed():
                while True:
                    current = await handle.get_current_frame()
                    if current is not None and current[2] != before[2]:
                        return
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(changed(), 3)

        try:
            for index in range(1, 4):
                clock[0] += 100_000
                await next_frame(frames[index], 10_000_000 + index * 100_000)
            await asyncio.wait_for(device.start_recording(str(output)), 3)
            for index in range(4, 8):
                clock[0] += 100_000
                await next_frame(frames[index], 10_000_000 + index * 100_000)
            clock[0] += 100_000
            await device.stop_recording()
        finally:
            await handle.disconnect_sockets()
        result = probe(output)
        assert "K" in result["packets"][0]["flags"]
        assert float(result["packets"][0]["pts_time"]) == 0
        assert float(result["streams"][0]["duration"]) == pytest.approx(0.5, abs=0.002)
        assert len(result["packets"]) >= 5

    asyncio.run(run())


def test_public_api_static_video_with_real_aac_stream(monkeypatch, synthetic, tmp_path):
    async def run():
        clock = [1_000_000]
        monkeypatch.setattr(recording, "monotonic_us", lambda: clock[0])
        device, handle, readers = await connected_device(monkeypatch, synthetic, audio=True)
        output = tmp_path / "public-audio.mp4"
        complete = asyncio.Event()
        consumed = 0
        original = handle.audio_stream.consume

        async def consume(data, pts):
            nonlocal consumed
            await original(data, pts)
            consumed += 1
            if consumed == 40:
                complete.set()

        handle.audio_stream.consume = consume
        try:
            await device.start_recording(str(output))
            for index, data in enumerate(synthetic[2][:40]):
                pts = 10_000_000 + index * 1024_000_000 // 48000
                readers[1].feed_data(media_packet(data, pts))
            await asyncio.wait_for(complete.wait(), 3)
            clock[0] += 1_000_000
            await device.stop_recording()
            assert handle.running, "stopping recording must keep the device session"
            assert await device.get_screenshot_jpg() is not None
        finally:
            await handle.disconnect_sockets()
        assert float(probe(output)["streams"][0]["duration"]) == pytest.approx(1, abs=0.002)
        assert probe(output, audio=True)["streams"][0]["codec_name"] == "aac"

    asyncio.run(run())


def test_public_api_receives_new_frames_and_auto_finishes_on_eof(monkeypatch, synthetic, tmp_path):
    async def run():
        clock = [1_000_000]
        monkeypatch.setattr(recording, "monotonic_us", lambda: clock[0])
        device, handle, readers = await connected_device(monkeypatch, synthetic, audio=False)
        output = tmp_path / "public-eof.mp4"
        try:
            await device.start_recording(str(output))
            clock[0] += 500_000
            readers[0].feed_data(media_packet(synthetic[0]["blue"][1], 10_500_000, key=True))

            async def blue_frame():
                while True:
                    frame = await handle.get_current_frame()
                    if frame is not None and frame[2][0] > 150:
                        return
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(blue_frame(), 3)
            clock[0] += 500_000
            readers[-1].feed_eof()
            await asyncio.wait_for(handle.wait_disconnected(), 5)
            await device.stop_recording()
        finally:
            await handle.disconnect_sockets()
        result = probe(output)
        assert float(result["streams"][0]["duration"]) == pytest.approx(1, abs=0.002)
        assert len(result["packets"]) >= 3, "new decoded frame did not reach the recorder"

    asyncio.run(run())
