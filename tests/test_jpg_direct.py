"""Direct JPEG contracts, using synthetic H.264 and no ADB/device access."""

import asyncio
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from adb_scr import AndroidDevice
from adb_scr.device.control_handle import DeviceControlHandle
from adb_scr.media_ext import _adb_scr_media as media
from adb_scr.media_ext.h264 import vtb_decoder


@pytest.fixture(scope="module")
def native_backend_probe(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path_factory.mktemp("jpeg-backend") / "probe"
    subprocess.run(
        [
            "clang",
            "-O2",
            "-fblocks",
            "-I" + str(root / "native_code/macOS/include"),
            "-I" + str(root / "native_code/macOS/src"),
            str(root / "tests/native_jpg_backend.m"),
            "-framework",
            "Foundation",
            "-framework",
            "CoreImage",
            "-framework",
            "Metal",
            "-framework",
            "VideoToolbox",
            "-framework",
            "CoreMedia",
            "-framework",
            "CoreVideo",
            "-framework",
            "ImageIO",
            "-framework",
            "CoreGraphics",
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    return binary


@pytest.mark.parametrize("backend", ["hardware", "fallback"])
def test_native_backend_reuse_and_fallback(native_backend_probe, backend):
    result = subprocess.run(
        [str(native_backend_probe), backend],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode == 77:
        pytest.skip("hardware JPEG encoder unavailable on this host")
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def encoded_frame(tmp_path_factory):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg required for synthetic media")
    path = tmp_path_factory.mktemp("jpeg-direct") / "quadrants.h264"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            (
                "color=red:s=96x64,"
                "drawbox=x=48:y=0:w=48:h=32:color=lime:t=fill,"
                "drawbox=x=0:y=32:w=48:h=32:color=blue:t=fill,"
                "drawbox=x=48:y=32:w=48:h=32:color=yellow:t=fill"
            ),
            "-vf",
            "scale=out_color_matrix=bt709",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-frames:v",
            "1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-f",
            "h264",
            str(path),
        ],
        check=True,
    )
    parts = [p for p in re.split(b"\x00\x00\x00?\x01", path.read_bytes()) if p]
    config = b"".join(b"\0\0\0\1" + p for p in parts if p[0] & 31 in (7, 8))
    idr = b"".join(b"\0\0\0\1" + p for p in parts if p[0] & 31 == 5)
    return config, idr


@pytest.fixture
def decoder(encoded_frame):
    config, idr = encoded_frame
    decoder = vtb_decoder.VtbH264Decoder(config)
    try:
        assert decoder.enqueue_frame(True, idr, 0)
        deadline = time.monotonic() + 3
        while media.get_current_frame_bgra8(decoder.handle) is None:
            assert time.monotonic() < deadline, "synthetic frame never decoded"
            time.sleep(0.001)
        yield decoder
    finally:
        asyncio.run(decoder.close_decoder())


def jpeg_size(jpeg):
    assert jpeg[:2] == b"\xff\xd8" and jpeg[-2:] == b"\xff\xd9"
    offset = 2
    while offset < len(jpeg):
        assert jpeg[offset] == 255
        marker = jpeg[offset + 1]
        length = int.from_bytes(jpeg[offset + 2 : offset + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            return (
                int.from_bytes(jpeg[offset + 7 : offset + 9], "big"),
                int.from_bytes(jpeg[offset + 5 : offset + 7], "big"),
            )
        offset += 2 + length
    pytest.fail("JPEG has no supported SOF marker")


def screenshot(decoder, *args, **kwargs):
    async def run():
        device = AndroidDevice("synthetic", "usb")
        handle = DeviceControlHandle("synthetic", "00000000")
        handle.running = True
        handle.h264_decoder = decoder
        device.control_handle = handle
        return await device.get_screenshot_jpg(*args, **kwargs)

    return asyncio.run(run())


@pytest.mark.parametrize(
    "options, expected",
    [
        ({}, (96, 64)),
        ({"quality": 1}, (96, 64)),
        ({"quality": 100}, (96, 64)),
        ({"scale": 0.5}, (48, 32)),
        ({"scale": 2.0}, (192, 128)),
        ({"scale": 0.00001}, (1, 1)),
        ({"roi": (3, 5, 31, 19)}, (31, 19)),
        ({"roi": (3, 5, 31, 19), "scale": 0.5}, (16, 10)),
        ({"roi": (3, 5, 31, 19), "scale": 1.5}, (47, 29)),
        ({"roi": (80, 50, 16, 14)}, (16, 14)),
    ],
)
def test_direct_jpeg_dimensions(decoder, options, expected):
    # Catches ignored ROI/scale, scale-before-crop and incorrect size rounding.
    jpeg = screenshot(decoder, **options)
    assert jpeg is not None
    assert jpeg_size(jpeg) == expected


@pytest.mark.parametrize(
    "roi, expected_rgb",
    [
        ((4, 4, 24, 16), (255, 0, 0)),
        ((60, 4, 24, 16), (0, 255, 0)),
        ((4, 40, 24, 16), (0, 0, 255)),
        ((60, 40, 24, 16), (255, 255, 0)),
    ],
)
def test_roi_uses_original_top_left_coordinates(decoder, roi, expected_rgb):
    jpeg = screenshot(decoder, quality=90, scale=0.5, roi=roi)
    assert jpeg_size(jpeg) == (12, 8)
    rgb = subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        input=jpeg,
        capture_output=True,
        check=True,
    ).stdout
    assert len(rgb) == 12 * 8 * 3
    center = rgb[(4 * 12 + 6) * 3 : (4 * 12 + 6) * 3 + 3]
    assert all(abs(a - b) < 35 for a, b in zip(center, expected_rgb)), center


@pytest.mark.parametrize(
    "options, error",
    [
        ({"quality": 0}, ValueError),
        ({"quality": 101}, ValueError),
        ({"quality": 75.5}, TypeError),
        ({"quality": True}, TypeError),
        ({"scale": 0}, ValueError),
        ({"scale": -1}, ValueError),
        ({"scale": float("nan")}, ValueError),
        ({"scale": float("inf")}, ValueError),
        ({"scale": "0.5"}, TypeError),
        ({"scale": True}, TypeError),
        ({"roi": (1, 2, 3)}, TypeError),
        ({"roi": (1, 2, 3.5, 4)}, TypeError),
        ({"roi": (-1, 0, 10, 10)}, ValueError),
        ({"roi": (0, 0, 0, 10)}, ValueError),
        ({"roi": (90, 0, 10, 10)}, ValueError),
        ({"roi": (0, 60, 10, 10)}, ValueError),
        ({"scale": 1e300}, ValueError),
    ],
)
def test_invalid_jpeg_options_raise(decoder, options, error):
    with pytest.raises(error):
        screenshot(decoder, **options)


def test_no_frame_and_closed_decoder_return_none(encoded_frame):
    async def run():
        device = AndroidDevice("synthetic", "usb")
        assert await device.get_screenshot_jpg() is None
        decoder = await asyncio.to_thread(vtb_decoder.VtbH264Decoder, encoded_frame[0])
        try:
            assert await decoder.get_current_frame_jpg() is None
        finally:
            await decoder.close_decoder()
        assert await decoder.get_current_frame_jpg() is None
        assert media.get_current_frame_jpg(decoder.handle) is None

    asyncio.run(run())


def test_jpeg_bytes_survive_decoder_close_and_legacy_path_still_works(decoder):
    jpeg = screenshot(decoder)
    frame = media.get_current_frame_bgra8(decoder.handle)
    assert frame[:2] == (96, 64)
    assert jpeg_size(media.bgra8_to_jpg(*frame, 75)) == (96, 64)
    asyncio.run(decoder.close_decoder())
    assert jpeg_size(jpeg) == (96, 64)


@pytest.mark.parametrize("positional", [True, False])
def test_existing_quality_only_calls_remain_supported(decoder, positional):
    jpeg = screenshot(decoder, 90) if positional else screenshot(decoder, quality=90)
    assert jpeg_size(jpeg) == (96, 64)


def test_reused_encoder_applies_each_requests_options(decoder):
    low = screenshot(decoder, quality=1)
    high = screenshot(decoder, quality=100)
    assert low != high, "quality changes were ignored by the cached encoder"
    assert jpeg_size(screenshot(decoder, scale=2, roi=(4, 4, 24, 16))) == (48, 32)
    with pytest.raises(ValueError):
        screenshot(decoder, roi=(95, 0, 2, 2))
    assert jpeg_size(screenshot(decoder)) == (96, 64)


def test_cancelled_jpeg_waits_for_worker_before_unlocking(decoder, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def encode(*args):
        entered.set()
        release.wait(3)
        return b"jpeg"

    monkeypatch.setattr(vtb_decoder, "get_current_frame_jpg", encode, raising=False)

    async def run():
        handle = DeviceControlHandle("synthetic", "00000000")
        handle.running = True
        handle.h264_decoder = decoder
        task = asyncio.create_task(handle.get_current_frame_jpg())
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            assert handle._mutex.locked()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not handle._mutex.locked()

    asyncio.run(run())
