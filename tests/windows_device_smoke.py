"""Manual, explicitly authorized Windows/ADB smoke test; never collected by pytest.

Run with uv run --python 3.14 --locked tests/windows_device_smoke.py --help.
Captures the phone's current screen/audio without injecting input gestures.
Output can contain private screen content; use an ignored/local directory.
"""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import statistics
import subprocess
import sys
import time
import traceback
from pathlib import Path

from adb_scr import AndroidDevice, deinit_lib, init_lib, list_devices
from adb_scr.media_ext import _adb_scr_media as media
from adb_scr.media_ext.h264.mf_decoder import MfH264Decoder


def load_probe(directory):
    directory.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("build_windows_probe.py")), str(directory)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    (directory / "build.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Probe build failed: {directory / 'build.log'}")
    path, = directory.glob("*.pyd")
    spec = importlib.util.spec_from_file_location("_adb_scr_media", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def frame_ready(device):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not device.is_connected:
            raise RuntimeError(f"Disconnected: {device.last_disconnect_reason}")
        frame = await device.control_handle.get_current_frame()
        if frame is not None:
            return frame
        await asyncio.sleep(0.05)
    raise RuntimeError("No decoded phone frame within 10 seconds")


def check_frame(frame):
    w, h, pixels = frame
    assert len(pixels) == w * h * 4, (w, h, len(pixels))
    assert pixels[3::4] == b"\xff" * (w * h), "BGRA alpha is not opaque"
    return [w, h]


def timing(values):
    return {"count": len(values), "median_ms": round(statistics.median(values), 3),
            "max_ms": round(max(values), 3)}


def inspect_recording(probe, path, source_audio, origin, end, duration):
    video = probe.test_read_mp4(str(path))
    assert video and video[0][0] == 0, "Missing zero-time video"
    assert all(b[0] > a[0] for a, b in zip(video, video[1:])), "Video timestamps not increasing"
    video_end = max(p + d for p, d, _ in video) / 10_000_000
    assert abs(video_end - duration) < 0.05, (video_end, duration)
    decoded = probe.test_decode_mp4_summary(str(path))
    assert decoded["samples"] == len(video), (decoded, len(video))
    result = {"file": str(path), "bytes": path.stat().st_size,
              "video_packets": len(video), "video_duration_s": video_end,
              "expected_duration_s": duration, "decoded_video": decoded}
    expected = [data for pts, data in source_audio if origin <= pts < end]
    if expected:
        audio = probe.test_read_mp4(str(path), True)
        assert [data for _, _, data in audio] == expected, "AAC passthrough bytes differ"
        decoded_audio = probe.test_decode_mp4_summary(str(path), True)
        assert decoded_audio["samples"] > 0
        result.update(audio_packets=len(audio), aac_passthrough_exact=True,
                      decoded_audio=decoded_audio,
                      audio_first_pts_s=audio[0][0] / 10_000_000,
                      audio_end_s=max(p + d for p, d, _ in audio) / 10_000_000)
    else:
        result["audio_packets"] = 0
    return result


async def run(args, probe, report):
    device = None
    waiter = None
    video_input = []
    audio_input = []
    enqueue_original = MfH264Decoder.enqueue_frame
    audio_original = media.append_recording_audio

    def observe_video(self, is_idr, data, pts):
        accepted = enqueue_original(self, is_idr, data, pts)
        video_input.append((pts, len(data), is_idr, time.monotonic()))
        return accepted

    def observe_audio(handle, data, pts):
        result = audio_original(handle, data, pts)
        audio_input.append((pts, data))
        return result

    MfH264Decoder.enqueue_frame = observe_video
    media.append_recording_audio = observe_audio
    try:
        versions = await init_lib(args.adb)
        report["versions"] = {"adb": versions[0], "scrcpy": versions[1], "python": sys.version}
        devices = await list_devices()
        if len(devices) != 1:
            raise RuntimeError(f"Expected exactly one connected phone; found {len(devices)}")
        device = AndroidDevice(devices[0], "usb")
        started = time.perf_counter()
        assert await device.connect(), device.last_disconnect_reason
        report["connect_ms"] = round((time.perf_counter() - started) * 1000, 3)
        frame = await frame_ready(device)
        report["initial_dimensions"] = check_frame(frame)
        report["bgra_bytes"] = len(frame[2])
        print("CONNECTED", report["initial_dimensions"], flush=True)
        jpg = await device.get_screenshot_jpg()
        assert probe.test_read_jpeg(jpg)[:2] == frame[:2]
        (args.output / "screenshot.jpg").write_bytes(jpg)
        crop = await device.get_screenshot_jpg(90, 0.5, (1, 3, 101, 99))
        assert probe.test_read_jpeg(crop)[:2] == (51, 50)
        (args.output / "crop.jpg").write_bytes(crop)
        report["jpeg_roi"] = "101x99 at (1,3), scale 0.5 -> 51x50"
        waiter = asyncio.create_task(device.wait_media_error())
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert device.is_connected
        report["cancel_error_wait_keeps_connection"] = True
        waiter = asyncio.create_task(device.wait_media_error())
        path = args.output / "phone.mp4"
        recording_start = time.perf_counter()
        await device.start_recording(str(path))
        report["recording_start_ms"] = (time.perf_counter() - recording_start) * 1000
        controller = device.control_handle.recording
        origin = controller._source_origin
        baseline = len(video_input)
        started = time.monotonic()
        bgra_times, jpg_times, dimensions, hashes = [], [], set(), set()
        print("RECORDING", args.duration, "seconds", flush=True)
        while time.monotonic() - started < args.duration:
            if waiter.done():
                await waiter
                raise RuntimeError("Media waiter completed before disconnect")
            t = time.perf_counter()
            frame = await device.control_handle.get_current_frame()
            bgra_times.append((time.perf_counter() - t) * 1000)
            dimensions.add(tuple(check_frame(frame)))
            hashes.add(hashlib.sha256(frame[2]).hexdigest())
            t = time.perf_counter()
            jpg = await device.get_screenshot_jpg()
            jpg_times.append((time.perf_counter() - t) * 1000)
            assert jpg[:2] == b"\xff\xd8"
            await asyncio.sleep(0.05)
        await device.stop_recording()
        await device.stop_recording()
        end = controller._end_pts
        report["bgra"] = timing(bgra_times)
        report["jpeg"] = timing(jpg_times)
        report["sampled_dimensions"] = sorted(dimensions)
        report["distinct_sampled_frames"] = len(hashes)
        report["received_video_packets_during_recording"] = len(video_input) - baseline
        report["recording"] = await asyncio.to_thread(
            inspect_recording, probe, path, list(audio_input), origin, end, (end - origin) / 1_000_000,
        )
        assert device.is_connected and await device.get_screenshot_jpg()
        process = device.scrcpy_server_process
        started = time.perf_counter()
        await device.disconnect()
        await device.disconnect()
        await waiter
        waiter = None
        assert not device.is_connected and process.returncode is not None
        report["disconnect_ms"] = round((time.perf_counter() - started) * 1000, 3)
        print("MAIN RECORDING VERIFIED", flush=True)
        assert await device.connect(), device.last_disconnect_reason
        check_frame(await frame_ready(device))
        auto_path = args.output / "disconnect-finalize.mp4"
        audio_input.clear()
        await device.start_recording(str(auto_path))
        controller = device.control_handle.recording
        origin = controller._source_origin
        await asyncio.sleep(2)
        process = device.scrcpy_server_process
        await device.disconnect()
        await device.stop_recording()
        assert not device.is_connected and process.returncode is not None
        end = controller._end_pts
        report["disconnect_finalizes_recording"] = await asyncio.to_thread(
            inspect_recording, probe, auto_path, list(audio_input), origin, end, (end - origin) / 1_000_000,
        )
        report["passed"] = True
    finally:
        report["video_input_count"] = len(video_input)
        report["video_recent_timing"] = [(p - video_input[0][0], n, key, t - video_input[0][3])
                                          for p, n, key, t in video_input[-20:]]
        if device is not None:
            await device.disconnect()
        if waiter is not None:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
        await deinit_lib()
        MfH264Decoder.enqueue_frame = enqueue_original
        media.append_recording_audio = audio_original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=None, help="Explicit adb executable, or PATH by default")
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--output", type=Path, required=True, help="New local artifact directory")
    args = parser.parse_args()
    if sys.platform != "win32" or not 2 <= args.duration <= 300:
        parser.error("Requires Windows and duration 2..300 seconds")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"passed": False}
    try:
        probe = load_probe(args.output / "probe")
        asyncio.run(run(args, probe, report))
    except BaseException:
        report["error"] = traceback.format_exc()
        raise
    finally:
        (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
