"""Windows system codecs and native lifetime tests; no ADB and no FFmpeg."""

import ctypes
import gc
import importlib.util
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native media")


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("windows-native-probe")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("build_windows_probe.py")), str(directory)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    path, = directory.glob("*.pyd")
    spec = importlib.util.spec_from_file_location("_adb_scr_media", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def clip(probe):
    return probe.test_make_h264(130, 98, 4)


def frame_ready(media, handle):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        frame = media.get_current_frame_bgra8(handle)
        if frame is not None:
            return frame
        time.sleep(0.01)
    pytest.fail("no decoded frame before deadline")


@contextmanager
def decoder(media, clip):
    header, packets = clip
    w, h, handle = media.create_decoder(header)
    try:
        assert media.get_current_frame_bgra8(handle) is None
        assert media.enqueue_frame(handle, packets[0], 0)
        frame_ready(media, handle)
        yield w, h, handle
    finally:
        media.destroy_decoder(handle)


def pixel(frame, x, y):
    w, _, data = frame
    i = (y * w + x) * 4
    return tuple(data[i:i + 4])


def test_decode_exact_bgra_and_jpeg(probe, clip):
    from adb_scr.media_ext import _adb_scr_media as media
    with decoder(media, clip) as (w, h, handle):
        assert (w, h) == (130, 98)
        frame = frame_ready(media, handle)
        assert len(frame[2]) == w * h * 4
        for x, y, channel in [(10, 10, 2), (100, 10, 1), (10, 80, 0)]:
            color = pixel(frame, x, y)
            assert color[channel] > 220, color
            assert max(color[i] for i in range(3) if i != channel) < 40, color
            assert color[3] == 255
        jpg = media.get_current_frame_jpg(handle, 90, 0.5, (1, 3, 67, 49))
        decoded = probe.test_read_jpeg(jpg)
        assert decoded[:2] == (34, 25)
        assert pixel(decoded, 3, 3)[2] > 220
        assert media.get_current_frame_jpg(handle, 20) != media.get_current_frame_jpg(handle, 95)
        with pytest.raises(ValueError):
            media.get_current_frame_jpg(handle, 75, 1, (129, 0, 2, 2))
        media.destroy_decoder(handle)
        assert media.get_current_frame_bgra8(handle) is None
        assert not media.enqueue_frame(handle, clip[1][0], 0)
        assert len(frame[2]) == w * h * 4


@pytest.mark.parametrize("bottom_up", [False, True])
def test_padded_rows_are_packed_top_down(probe, bottom_up):
    stride, pixels = probe.test_copy_rows(131, 97, bottom_up)
    assert abs(stride) > 131 * 4
    assert len(pixels) == 131 * 97 * 4
    assert pixel((131, 97, pixels), 130, 96) == (130, 96, 31, 255)
    assert pixel((131, 97, pixels), 0, 0) == (0, 0, 31, 255)


@pytest.mark.parametrize("w,h", [(1080, 2400), (2400, 1080), (1440, 3200)])
def test_phone_dimensions(probe, w, h):
    from adb_scr.media_ext import _adb_scr_media as media
    clip = probe.test_make_h264(w, h, 1)
    with decoder(media, clip) as (actual_w, actual_h, handle):
        assert (actual_w, actual_h) == (w, h)
        assert len(frame_ready(media, handle)[2]) == w * h * 4


@pytest.mark.parametrize("attempt", range(4))
@pytest.mark.parametrize("end_pts", [0, 1000, 2_000_000])
def test_static_recording_has_full_duration_and_exclusive_path(probe, clip, tmp_path, attempt, end_pts):
    from adb_scr.media_ext import _adb_scr_media as media
    path = tmp_path / "静止画面.mp4"
    with decoder(media, clip) as (_, _, handle):
        recorder = media.start_recording(handle, str(path), None, 0, 30, 0.75)
        media.set_decoder_recording(handle, None)
        media.stop_recording(recorder, end_pts)
        media.stop_recording(recorder, end_pts)
        packets = probe.test_read_mp4(str(path))
        assert packets[0][0] == 0
        end = max(pts + duration for pts, duration, _ in packets)
        assert abs(end - max(1000, end_pts) * 10) <= 1000, [(p, d) for p, d, _ in packets]
        before = path.read_bytes()
        with pytest.raises(RuntimeError):
            media.start_recording(handle, str(path), None, 0, 30)
        assert path.read_bytes() == before


def test_jpeg_and_native_handles_are_thread_safe(probe, clip):
    from adb_scr.media_ext import _adb_scr_media as media
    def work(_):
        with decoder(media, clip) as (_, _, handle):
            for _ in range(3):
                assert media.get_current_frame_jpg(handle).startswith(b"\xff\xd8")
            return frame_ready(media, handle)[2]
    with ThreadPoolExecutor(max_workers=4) as pool:
        frames = list(pool.map(work, range(12)))
    gc.collect()
    assert all(len(frame) == 130 * 98 * 4 for frame in frames)


def test_aac_passthrough_keeps_packets_and_initial_offset(probe, clip, tmp_path):
    from adb_scr.media_ext import _adb_scr_media as media
    config, audio = probe.test_make_aac()
    path = tmp_path / "audio.mp4"
    with decoder(media, clip) as (_, _, handle):
        recorder = media.start_recording(handle, str(path), config, 0, 30)
        for i, packet in enumerate(audio):
            media.append_recording_audio(recorder, packet, 100_000 + i * 1024_000_000 // 48000)
        media.set_decoder_recording(handle, None)
        media.stop_recording(recorder, 1_000_000)
    result = probe.test_read_mp4(str(path), True)
    assert [p for _, _, p in result] == audio
    assert abs(result[0][0] - 1_000_000) <= 210
    assert all(abs(pts - (1_000_000 + i * 1024 * 10_000_000 // 48000)) <= 210
               for i, (pts, _, _) in enumerate(result))


def test_static_picture_with_continuous_audio_does_not_stall_writer(probe, clip, tmp_path):
    config, audio = probe.test_make_aac(300)
    path = tmp_path / "static-with-audio.mp4"
    with decoder(probe, clip) as (_, _, handle):
        recorder = probe.start_recording(handle, str(path), config, 0, 30)
        started = time.monotonic()
        for i, packet in enumerate(audio):
            target = started + i * 1024 / 48000
            time.sleep(max(0, target - time.monotonic()))
            probe.append_recording_audio(recorder, packet, i * 1024_000_000 // 48000)
            probe.check_recording_error(recorder)
        probe.set_decoder_recording(handle, None)
        probe.stop_recording(recorder, 6_400_000)
    assert [p for _, _, p in probe.test_read_mp4(str(path), True)] == audio
    video = probe.test_decode_mp4_summary(str(path))
    assert video["samples"] > 2
    assert abs(video["end_100ns"] - 64_000_000) < 1000
    decoded_audio = probe.test_decode_mp4_summary(str(path), True)
    assert decoded_audio["samples"] == len(audio)
    assert decoded_audio["pcm_peak"] > 0


def test_video_later_than_committed_static_segment_fails_instead_of_dropping(probe, clip, tmp_path):
    from adb_scr.exceptions import MediaPipelineOverloadedError
    config, audio = probe.test_make_aac(75)
    with decoder(probe, clip) as (_, _, handle):
        recorder = probe.start_recording(handle, str(tmp_path / "late-video.mp4"), config, 0, 30)
        for i, packet in enumerate(audio):
            probe.append_recording_audio(recorder, packet, i * 1024_000_000 // 48000)
            probe.test_wait_recording(recorder)
        probe.enqueue_frame(handle, clip[1][0], 200_000)
        probe.set_decoder_recording(handle, None)
        with pytest.raises(MediaPipelineOverloadedError, match="500 ms"):
            probe.stop_recording(recorder, 2_000_000)
        assert probe.get_current_frame_bgra8(handle)


@pytest.mark.parametrize("offset", [100_000, -40_000])
def test_aac_gap_or_overlap_is_a_persistent_failure(probe, clip, tmp_path, offset):
    from adb_scr.media_ext import _adb_scr_media as media
    config, audio = probe.test_make_aac()
    with decoder(media, clip) as (_, _, handle):
        recorder = media.start_recording(handle, str(tmp_path / "gap.mp4"), config, 0, 30)
        for i in range(3):
            media.append_recording_audio(recorder, audio[i], i * 1024_000_000 // 48000)
        media.append_recording_audio(recorder, audio[3], 3 * 1024_000_000 // 48000 + offset)
        media.set_decoder_recording(handle, None)
        with pytest.raises(RuntimeError, match="AAC timestamp"):
            media.stop_recording(recorder, 1_000_000)
        with pytest.raises(RuntimeError, match="AAC timestamp"):
            media.check_recording_error(recorder)
        with pytest.raises(RuntimeError, match="AAC timestamp"):
            media.stop_recording(recorder, 1_000_000)


@contextmanager
def blocked_queue(media, handle, recording=False):
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    api.CreateEventW.restype = ctypes.c_void_p
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    api.SetEvent.argtypes = [ctypes.c_void_p]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    entered = api.CreateEventW(None, True, False, None)
    release = api.CreateEventW(None, True, False, None)
    assert entered and release
    try:
        block = media.test_block_recording if recording else media.test_block
        block(handle, entered, release)
        assert api.WaitForSingleObject(entered, 2000) == 0
        yield
    finally:
        api.SetEvent(release)
        # The marker has left WaitForSingleObject before its event handles close.
        if recording:
            try:
                media.stop_recording(handle, 1_000_000)
            except RuntimeError:
                pass
        else:
            media.destroy_decoder(handle)
        api.CloseHandle(entered)
        api.CloseHandle(release)


def test_decoder_overload_fails_and_stays_failed_after_close(probe, clip):
    from adb_scr.exceptions import MediaPipelineOverloadedError
    _, _, handle = probe.create_decoder(clip[0])
    with blocked_queue(probe, handle):
        for i in range(31):
            assert probe.enqueue_frame(handle, clip[1][0], i * 33333)
        with pytest.raises(MediaPipelineOverloadedError, match="decode queue overloaded"):
            probe.enqueue_frame(handle, clip[1][0], 32 * 33333)
        with pytest.raises(MediaPipelineOverloadedError):
            probe.get_current_frame_bgra8(handle)
    with pytest.raises(MediaPipelineOverloadedError):
        probe.check_decoder_error(handle)


def test_delayed_decoder_output_without_another_input(probe, clip):
    _, _, handle = probe.create_decoder(clip[0])
    try:
        # A completed input job may not have produced output yet. No more packets
        # arrive on a static screen; the worker must continue retrieving output.
        probe.test_delay_decoder_output(handle, 20)
        assert probe.enqueue_frame(handle, clip[1][0], 0)
        frame = frame_ready(probe, handle)
        assert len(frame[2]) == 130 * 98 * 4
        assert pixel(frame, 10, 10)[2] > 220
    finally:
        probe.destroy_decoder(handle)


def test_decoder_budget_includes_input_already_accepted_by_mft(probe, clip):
    from adb_scr.exceptions import MediaPipelineOverloadedError
    _, _, handle = probe.create_decoder(clip[0])
    probe.test_delay_decoder_output(handle, 0xFFFFFFFF)
    probe.enqueue_frame(handle, clip[1][0], 0)
    probe.set_decoder_recording(handle, None)  # Input job has completed.
    with blocked_queue(probe, handle):
        for i in range(31):
            probe.enqueue_frame(handle, clip[1][0], (i + 1) * 33333)
        with pytest.raises(MediaPipelineOverloadedError, match="decode input backlog"):
            probe.enqueue_frame(handle, clip[1][0], 32 * 33333)
    with pytest.raises(MediaPipelineOverloadedError):
        probe.check_decoder_error(handle)


def test_stalled_decoder_output_expires_without_new_input(probe, clip):
    from adb_scr.exceptions import MediaPipelineOverloadedError
    _, _, handle = probe.create_decoder(clip[0])
    try:
        probe.test_delay_decoder_output(handle, 0xFFFFFFFF)
        probe.enqueue_frame(handle, clip[1][0], 0)
        probe.test_delay_decoder_output(handle, 0xFFFFFFFF, True)
        deadline = time.monotonic() + 2
        with pytest.raises(MediaPipelineOverloadedError, match="decode output exceeded 5000 ms"):
            while time.monotonic() < deadline:
                probe.check_decoder_error(handle)
                time.sleep(0.01)
        with pytest.raises(MediaPipelineOverloadedError):
            probe.get_current_frame_bgra8(handle)
    finally:
        probe.destroy_decoder(handle)
    with pytest.raises(MediaPipelineOverloadedError):
        probe.check_decoder_error(handle)


def test_recording_video_overload_does_not_kill_decoder(probe, clip, tmp_path):
    from adb_scr.exceptions import MediaPipelineOverloadedError
    with decoder(probe, clip) as (_, _, handle):
        recorder = probe.start_recording(handle, str(tmp_path / "overload.mp4"), None, 0, 30)
        with blocked_queue(probe, recorder, recording=True):
            for i in range(9):
                probe.enqueue_frame(handle, clip[1][0], (i + 1) * 33333)
                probe.set_decoder_recording(handle, recorder)  # Wait for this decoder input.
            with pytest.raises(MediaPipelineOverloadedError, match="limit=8"):
                probe.check_recording_error(recorder)
            probe.check_decoder_error(handle)
            assert probe.get_current_frame_bgra8(handle)
            probe.set_decoder_recording(handle, None)


def test_recording_audio_byte_budget(probe, clip, tmp_path):
    from adb_scr.exceptions import MediaPipelineOverloadedError
    config, _ = probe.test_make_aac()
    with decoder(probe, clip) as (_, _, handle):
        recorder = probe.start_recording(handle, str(tmp_path / "budget.mp4"), config, 0, 30)
        with blocked_queue(probe, recorder, recording=True):
            for i in range(4):
                probe.append_recording_audio(recorder, bytes(1024 * 1024), i * 21333)
            with pytest.raises(MediaPipelineOverloadedError, match="4 MiB"):
                probe.append_recording_audio(recorder, b"x", 5 * 21333)
            probe.set_decoder_recording(handle, None)


@pytest.mark.parametrize("expired", [False, True])
def test_stalled_sink_writer_fails_without_unbounded_buffering(probe, clip, tmp_path, expired):
    from adb_scr.exceptions import MediaPipelineOverloadedError
    with decoder(probe, clip) as (_, _, handle):
        recorder = probe.start_recording(handle, str(tmp_path / "stalled-sink.mp4"), None, 0, 30)
        # Simulate the system no longer reporting completed samples. Real input,
        # writer calls, periodic monitoring and teardown still execute normally.
        probe.test_stall_sink_statistics(recorder, expired)
        if not expired:
            for i in range(32):
                probe.enqueue_frame(handle, clip[1][0], (i + 1) * 33333)
                probe.set_decoder_recording(handle, recorder)
                if i < 31:
                    probe.test_wait_recording(recorder)
        else:
            probe.check_recording_error(recorder)  # No new video/audio is required.
        with pytest.raises(MediaPipelineOverloadedError, match="sink writer backlog"):
            probe.test_wait_recording(recorder)
        with pytest.raises(MediaPipelineOverloadedError, match="sink writer backlog"):
            probe.check_recording_error(recorder)
        probe.check_decoder_error(handle)
        assert probe.get_current_frame_bgra8(handle)
        probe.set_decoder_recording(handle, None)
        with pytest.raises(MediaPipelineOverloadedError, match="sink writer backlog"):
            probe.stop_recording(recorder, 2_000_000)


def test_predictive_frames_and_rotation_recording(probe, tmp_path):
    clip = probe.test_make_h264(130, 98, 6, False)
    rotated = probe.test_make_h264(98, 130, 4, False)
    path = tmp_path / "rotate.mp4"
    with decoder(probe, clip) as (_, _, handle):
        recorder = probe.start_recording(handle, str(path), None, 0, 30)
        for i, packet in enumerate(clip[1][1:], 1):
            probe.enqueue_frame(handle, packet, i * 33333)
            probe.set_decoder_recording(handle, recorder)
            probe.test_wait_recording(recorder)
        probe.set_decoder_recording(handle, None)
        with decoder(probe, rotated) as (_, _, other):
            probe.set_decoder_recording(other, recorder)
            for i, packet in enumerate(rotated[1][1:], 1):
                probe.enqueue_frame(other, packet, 300_000 + i * 33333)
                probe.set_decoder_recording(other, recorder)
                probe.test_wait_recording(recorder)
            probe.set_decoder_recording(other, None)
        probe.stop_recording(recorder, 1_000_000)
    packets = probe.test_read_mp4(str(path))
    assert len(packets) >= 9
    assert all(a[0] < b[0] for a, b in zip(packets, packets[1:]))
    # Extract SPS/PPS only: using the whole packet as config would prepend a
    # second copy of its IDR, submitting two pictures as one input sample.
    config = b"".join(b"\0\0\0\1" + nal for nal in re.split(b"\0\0\0?\1", packets[0][2])
                      if nal and nal[0] & 31 in (7, 8))
    with decoder(probe, (config, [packets[0][2]])) as (w, h, readback):
        assert (w, h) == (130, 98)
        for pts, _, packet in packets[1:]:
            probe.enqueue_frame(readback, packet, pts // 10)
            probe.set_decoder_recording(readback, None)
        frame = probe.get_current_frame_bgra8(readback)
        assert max(pixel(frame, 2, 40)[:3]) < 20  # Portrait frame has black side bars.
        assert pixel(frame, 30, 10)[2] > 180


def test_system_memory_media_without_d3d_manager(probe, clip, tmp_path):
    directory = tmp_path / "cpu-probe"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("build_windows_probe.py")), str(directory), "--cpu"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    path, = directory.glob("*.pyd")
    spec = importlib.util.spec_from_file_location("_adb_scr_media", path)
    cpu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cpu)
    with decoder(cpu, clip) as (w, h, handle):
        frame = cpu.get_current_frame_bgra8(handle)
        assert len(frame[2]) == w * h * 4
        assert pixel(frame, 10, 10)[2] > 220
        assert cpu.get_current_frame_jpg(handle).startswith(b"\xff\xd8")
        recorder = cpu.start_recording(handle, str(tmp_path / "cpu.mp4"), None, 0, 30)
        cpu.set_decoder_recording(handle, None)
        cpu.stop_recording(recorder, 1_000_000)
    assert probe.test_read_mp4(str(tmp_path / "cpu.mp4"))
    # Exercise software MFT scheduling even when this runner has a usable GPU.
    def read_first_frame(_):
        with decoder(cpu, clip) as (_, _, handle):
            return cpu.get_current_frame_jpg(handle)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(jpg.startswith(b"\xff\xd8") for jpg in pool.map(read_first_frame, range(48)))


def test_close_races_with_shared_handle_readers(probe, clip):
    from threading import Barrier
    with decoder(probe, clip) as (_, _, handle):
        ready = Barrier(5)
        def reader(_):
            ready.wait()
            for _ in range(5):
                frame = probe.get_current_frame_bgra8(handle)
                if frame is not None:
                    assert len(frame[2]) == 130 * 98 * 4
                jpeg = probe.get_current_frame_jpg(handle)
                assert jpeg is None or jpeg.startswith(b"\xff\xd8")
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(reader, i) for i in range(4)]
            ready.wait()
            probe.destroy_decoder(handle)
            for future in futures:
                future.result(timeout=10)


@pytest.mark.parametrize("config", [b"", b"\x00\x00\x00\x01\x67", b"\x00\x00\x00\x01\x67" + b"\0" * 32])
def test_invalid_sps_fails_without_a_worker_leak(probe, config):
    for _ in range(4):
        with pytest.raises(RuntimeError):
            probe.create_decoder(config)
