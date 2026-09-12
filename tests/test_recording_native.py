"""Real VideoToolbox/AVFoundation recording tests using synthetic media only."""

import concurrent.futures
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from adb_scr.media_ext import _adb_scr_media as media


@pytest.fixture(scope="module")
def apple_reader(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path_factory.mktemp("apple-reader") / "reader"
    subprocess.run(["clang", str(root / "tests/recording_reader_probe.m"),
                    "-framework", "AVFoundation", "-framework", "CoreMedia",
                    "-framework", "Foundation", "-o", str(binary)],
                   check=True, capture_output=True)
    return lambda path: json.loads(subprocess.check_output([str(binary), str(path)], timeout=10))


def test_native_recording_api_exists():
    assert hasattr(media, "start_recording"), "native recording API missing"


@pytest.mark.parametrize("quality", [0.0, 0.4, 0.75, 1.0])
def test_native_recording_accepts_quality(synthetic, tmp_path, quality):
    decoder = decoder_for(synthetic)
    try:
        path = tmp_path / "quality.mp4"
        recording = media.start_recording(decoder, str(path), None, 5_000_000, 30, quality)
        media.set_decoder_recording(decoder, None)
        media.stop_recording(recording, 6_000_000)
        assert probe(path)["streams"][0]["codec_name"] == "h264"
    finally:
        media.destroy_decoder(decoder)


@pytest.mark.parametrize("quality, error", [
    (-0.01, ValueError), (1.01, ValueError), (float("nan"), ValueError),
    (float("inf"), ValueError), (True, TypeError), ("0.75", TypeError), (None, TypeError),
])
def test_native_recording_rejects_invalid_quality_before_file_creation(tmp_path, quality, error):
    path = tmp_path / "invalid.mp4"
    with pytest.raises(error, match="quality"):
        media.start_recording(None, str(path), None, 0, 30, quality)
    assert not path.exists()


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe required for synthetic recording fixtures")
    directory = tmp_path_factory.mktemp("recording-media")
    frames = {}
    for name, size, color in [("red", "64x64", "red"), ("blue", "64x64", "blue"),
                              ("portrait", "64x128", "lime")]:
        path = directory / (name + ".h264")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=" + color + ":size=" + size + ":rate=30",
                        "-frames:v", "1", "-vf", "colorspace=all=bt709:iall=bt601-6-625",
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-tune", "zerolatency", "-threads", "1", "-f", "h264", str(path)], check=True)
        parts = [p for p in re.split(b"\x00\x00\x00?\x01", path.read_bytes()) if p]
        config = b"".join(b"\0\0\0\1" + p for p in parts if p[0] & 31 in (7, 8))
        idr = b"".join(b"\0\0\0\1" + p for p in parts if p[0] & 31 == 5)
        frames[name] = config, idr
    audio = directory / "tone.aac"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:duration=1", "-ac", "2",
                    "-c:a", "aac", "-b:a", "64k", "-f", "adts", str(audio)], check=True)
    payload = audio.read_bytes()
    packets = []
    offset = 0
    while offset < len(payload):
        header = payload[offset:offset + 7]
        size = ((header[3] & 3) << 11) | (header[4] << 3) | (header[5] >> 5)
        packets.append(payload[offset + 7:offset + size])
        offset += size
    return frames, bytes.fromhex("1190"), packets


def decoder_for(synthetic, name="red", pts=1_000_000):
    config, frame = synthetic[0][name]
    result = media.create_decoder(config)
    assert result is not None
    decoder = result[2]
    assert media.enqueue_frame(decoder, frame, pts)
    deadline = time.monotonic() + 3
    while media.get_current_frame_bgra8(decoder) is None:
        assert time.monotonic() < deadline, "synthetic decoder produced no frame"
        time.sleep(0.001)
    return decoder


def probe(path, audio=False):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-show_packets",
        "-show_data_hash", "sha256", "-select_streams", "a" if audio else "v",
        "-of", "json", str(path)]))


def test_static_frame_starts_keyframe_zero_and_covers_stop_time(synthetic, tmp_path):
    decoder = decoder_for(synthetic)
    path = tmp_path / "static.mp4"
    recording = media.start_recording(decoder, str(path), None, 5_000_000, 30)
    assert path.exists(), "start must create the file before returning"
    media.set_decoder_recording(decoder, None)
    media.stop_recording(recording, 6_000_000)
    media.stop_recording(recording, 6_000_000)
    media.destroy_decoder(decoder)
    result = probe(path)
    assert result["streams"][0]["codec_name"] == "h264"
    assert result["streams"][0]["width"] == 64
    assert result["packets"][0]["pts_time"] == "0.000000"
    assert "K" in result["packets"][0]["flags"]
    assert float(result["streams"][0]["duration"]) == pytest.approx(1, abs=0.002)


def test_audio_preserves_access_units_and_source_timestamps(synthetic, tmp_path, apple_reader):
    decoder = decoder_for(synthetic)
    path = tmp_path / "audio.mp4"
    recording = media.start_recording(decoder, str(path), synthetic[1], 10_000_000, 30)
    for index, packet in enumerate(synthetic[2][:40]):
        media.append_recording_audio(recording, packet, 10_100_000 + index * 1024_000_000 // 48000)
    media.set_decoder_recording(decoder, None)
    media.stop_recording(recording, 11_000_000)
    media.destroy_decoder(decoder)
    result = probe(path, audio=True)
    assert result["streams"][0]["codec_name"] == "aac"
    assert result["streams"][0]["sample_rate"] == "48000"
    assert result["streams"][0]["channels"] == 2
    assert float(result["packets"][0]["pts_time"]) == pytest.approx(0.1, abs=0.002)
    assert [p["data_hash"] for p in result["packets"]] == [
        "SHA256:" + hashlib.sha256(packet).hexdigest() for packet in synthetic[2][:40]]
    ranges = apple_reader(path)["ranges"]
    assert ranges[0][0] == pytest.approx(0.1, abs=0.001)
    assert sum(ranges[-1]) == pytest.approx(0.1 + 40 * 1024 / 48000, abs=0.001)


def test_start_rejects_no_frame_existing_path_and_bad_audio(synthetic, tmp_path):
    empty = media.create_decoder(synthetic[0]["red"][0])[2]
    with pytest.raises(RuntimeError, match="frame"):
        media.start_recording(empty, str(tmp_path / "empty.mp4"), None, 0, 30)
    media.destroy_decoder(empty)
    decoder = decoder_for(synthetic)
    existing = tmp_path / "existing.mp4"
    existing.write_bytes(b"preserve")
    with pytest.raises((FileExistsError, RuntimeError)):
        media.start_recording(decoder, str(existing), None, 0, 30)
    assert existing.read_bytes() == b"preserve"
    with pytest.raises((ValueError, RuntimeError)):
        media.start_recording(decoder, str(tmp_path / "bad.mp4"), b"bad", 0, 30)
    media.destroy_decoder(decoder)


def test_rotation_rebind_and_decoder_destroy_preserve_recording(synthetic, tmp_path):
    decoder = decoder_for(synthetic)
    path = tmp_path / "rotation.mp4"
    recording = media.start_recording(decoder, str(path), None, 1_000_000, 30)
    media.set_decoder_recording(decoder, None)
    media.destroy_decoder(decoder)
    rotated = decoder_for(synthetic, "portrait", 1_500_000)
    media.set_decoder_recording(rotated, recording)
    assert media.enqueue_frame(rotated, synthetic[0]["portrait"][1], 1_500_000)
    # Reading synchronizes the decoder queue; allow the hardware output callback too.
    time.sleep(0.1)
    media.get_current_frame_bgra8(rotated)
    media.destroy_decoder(rotated)
    media.stop_recording(recording, 2_000_000)
    result = probe(path)
    assert (result["streams"][0]["width"], result["streams"][0]["height"]) == (64, 64)
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-sseof", "-0.05", "-i",
                                   str(path), "-frames:v", "1", "-f", "rawvideo",
                                   "-pix_fmt", "rgb24", "-"])
    assert len(raw) == 64 * 64 * 3
    assert max(raw[0:3]) < 20
    center = (32 * 64 + 32) * 3
    assert raw[center + 1] > 180 and raw[center] < 60


def test_rotation_fit_preserves_nv12_planes_and_black_levels(tmp_path):
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path / "recording-fit"
    command = ["clang", "-O3", "-fblocks",
               "-I" + str(root / "native_code/macOS/include"),
               "-I" + str(root / "native_code/macOS/src"),
               str(root / "tests/recording_fit_probe.m"), "-o", str(binary)]
    for framework in ["AVFoundation", "AudioToolbox", "CoreMedia", "CoreVideo",
                      "CoreFoundation", "Foundation", "CoreImage", "VideoToolbox",
                      "CoreGraphics", "Metal"]:
        command.extend(["-framework", framework])
    subprocess.run(command, check=True, capture_output=True)
    result = subprocess.run([str(binary)], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_concurrent_stop_close_and_repeated_recordings(synthetic, tmp_path):
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        for index in range(4):
            decoder = decoder_for(synthetic)
            path = tmp_path / (str(index) + ".mp4")
            recording = media.start_recording(decoder, str(path), None, 1_000_000, 30)
            futures = [pool.submit(media.stop_recording, recording, 1_050_000),
                       pool.submit(media.destroy_decoder, decoder),
                       pool.submit(media.stop_recording, recording, 1_050_000),
                       pool.submit(media.get_current_frame_jpg, decoder)]
            for future in futures:
                future.result(timeout=10)
            assert float(probe(path)["format"]["duration"]) == pytest.approx(0.05, abs=0.002)


@pytest.mark.parametrize("count", [1, 2, 3, 4, 40])
@pytest.mark.parametrize("offset", [0, 100_000])
def test_aac_short_and_zero_offset_preserve_every_packet(synthetic, tmp_path, count, offset):
    decoder = decoder_for(synthetic)
    output = tmp_path / "short-audio.mp4"
    recording = media.start_recording(decoder, str(output), synthetic[1], 1_000_000, 30)
    for index, packet in enumerate(synthetic[2][:count]):
        media.append_recording_audio(recording, packet,
                                     1_000_000 + offset + index * 1024_000_000 // 48000)
    media.set_decoder_recording(decoder, None)
    media.stop_recording(recording, 2_000_000)
    media.destroy_decoder(decoder)
    packets = probe(output, audio=True)["packets"]
    assert [packet["data_hash"] for packet in packets] == [
        "SHA256:" + hashlib.sha256(packet).hexdigest() for packet in synthetic[2][:count]]
    assert [float(packet["pts_time"]) for packet in packets] == pytest.approx([
        offset / 1_000_000 + index * 1024 / 48000 for index in range(count)], abs=0.001)


def test_stop_boundary_trims_video_already_queued_after_end(synthetic, tmp_path):
    decoder = decoder_for(synthetic)
    path = tmp_path / "stop-boundary.mp4"
    recording = media.start_recording(decoder, str(path), None, 1_000_000, 30)
    assert media.enqueue_frame(decoder, synthetic[0]["blue"][1], 2_200_000)
    media.destroy_decoder(decoder)  # drains callback + subscription before stop
    media.stop_recording(recording, 2_000_000)
    assert float(probe(path)["format"]["duration"]) == pytest.approx(1, abs=0.002)


def test_audio_config_without_packets_still_finishes_video(synthetic, tmp_path):
    decoder = decoder_for(synthetic)
    output = tmp_path / "no-audio.mp4"
    recording = media.start_recording(decoder, str(output), synthetic[1], 1_000_000, 30)
    media.set_decoder_recording(decoder, None)
    media.stop_recording(recording, 2_000_000)
    media.destroy_decoder(decoder)
    assert float(probe(output)["format"]["duration"]) == pytest.approx(1, abs=0.002)


def test_first_audio_packets_keep_timestamp_gaps(synthetic, tmp_path, apple_reader):
    decoder = decoder_for(synthetic)
    path = tmp_path / "audio-gap.mp4"
    recording = media.start_recording(decoder, str(path), synthetic[1], 1_000_000, 30)
    timestamps = [100_000, 150_000, 200_000, 221_333, 242_667]
    for packet, pts in zip(synthetic[2], timestamps):
        media.append_recording_audio(recording, packet, 1_000_000 + pts)
    media.set_decoder_recording(decoder, None)
    media.stop_recording(recording, 2_000_000)
    media.destroy_decoder(decoder)
    result = probe(path, audio=True)
    packets = result["packets"]
    # Empty edits need decoder preroll references; every source AU still has a
    # presentation at its original timestamp and raw storage remains unchanged.
    for packet, pts in zip(synthetic[2], timestamps):
        digest = "SHA256:" + hashlib.sha256(packet).hexdigest()
        times = [float(sample["pts_time"]) for sample in packets if sample["data_hash"] == digest]
        assert min(abs(value - pts / 1_000_000) for value in times) < 0.001
    raw = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-ignore_editlist", "1", "-show_packets",
        "-show_data_hash", "sha256", "-select_streams", "a", "-of", "json", str(path)]))["packets"]
    assert [packet["data_hash"] for packet in raw[:5]] == [
        "SHA256:" + hashlib.sha256(packet).hexdigest() for packet in synthetic[2][:5]]
    assert len(raw) == 8  # five source AUs and the three trimmed completion AUs
    assert result["format"]["tags"]["major_brand"] in {"mp42", "isom", "mp41"}
    ranges = apple_reader(path)["ranges"]
    assert ranges[0] == pytest.approx([0.1, 0.021333], abs=0.001)
    assert ranges[1] == pytest.approx([0.15, 0.021333], abs=0.001)
    assert ranges[2][0] == pytest.approx(0.2, abs=0.001)
    assert sum(ranges[-1]) == pytest.approx(0.264, abs=0.001)
    assert float(result["format"]["duration"]) == pytest.approx(1, abs=0.002)
    assert list(tmp_path.glob(".*.recording-*.mp4")) == []


def test_audio_source_clock_jitter_stays_within_one_packet(synthetic, tmp_path):
    decoder = decoder_for(synthetic)
    path = tmp_path / "audio-jitter.mp4"
    recording = media.start_recording(decoder, str(path), synthetic[1], 1_000_000, 30)
    timestamps = [100_000 + index * 1024_000_000 // 48000 + (index % 3) * 4_000
                  for index in range(20)]
    for packet, pts in zip(synthetic[2], timestamps):
        media.append_recording_audio(recording, packet, 1_000_000 + pts)
    media.set_decoder_recording(decoder, None)
    media.stop_recording(recording, 2_000_000)
    media.destroy_decoder(decoder)
    packets = probe(path, audio=True)["packets"]
    assert [packet["data_hash"] for packet in packets] == [
        "SHA256:" + hashlib.sha256(packet).hexdigest() for packet in synthetic[2][:20]]
    assert [float(packet["pts_time"]) for packet in packets] == pytest.approx(
        [pts / 1_000_000 for pts in timestamps], abs=1024 / 48000)


def test_large_audio_timestamp_overlap_remains_a_stop_error(synthetic, tmp_path):
    decoder = decoder_for(synthetic)
    recording = media.start_recording(decoder, str(tmp_path / "overlap.mp4"),
                                      synthetic[1], 1_000_000, 30)
    for packet, pts in zip(synthetic[2], [1_000_000, 1_005_000, 1_010_000]):
        media.append_recording_audio(recording, packet, pts)
    media.set_decoder_recording(decoder, None)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="overlap"):
            media.stop_recording(recording, 2_000_000)
    media.destroy_decoder(decoder)


def test_recording_queue_stays_bounded_and_failure_drains(synthetic, tmp_path):
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path / "recording-queue"
    command = ["clang", "-fblocks", "-I" + str(root / "native_code/macOS/include"),
               "-I" + str(root / "native_code/macOS/src"),
               str(root / "tests/recording_queue_probe.m"), "-o", str(binary)]
    for framework in ["AVFoundation", "AudioToolbox", "CoreMedia", "CoreVideo",
                      "CoreFoundation", "Foundation", "CoreImage", "VideoToolbox", "CoreGraphics", "Metal"]:
        command.extend(["-framework", framework])
    subprocess.run(command, check=True, capture_output=True)
    packet = tmp_path / "packet.aac"
    packet.write_bytes(synthetic[2][0])
    result = subprocess.run([str(binary), str(tmp_path / "bounded.mp4"), str(packet)],
                            check=False, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
