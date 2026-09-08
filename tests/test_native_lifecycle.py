"""Synthetic media only; native failures are isolated in a subprocess."""

import asyncio
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from adb_scr.media_ext.h264 import vtb_decoder


@pytest.fixture(scope="module")
def h264(tmp_path_factory):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg required to generate synthetic H.264")
    path = tmp_path_factory.mktemp("synthetic") / "frame.h264"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=1",
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
    return path


def test_native_close_is_idempotent_and_closed_handle_is_safe(h264):
    script = """
import re, sys
from pathlib import Path
from adb_scr.media_ext import _adb_scr_media as m
parts = [p for p in re.split(b"\\x00\\x00\\x00?\\x01", Path(sys.argv[1]).read_bytes()) if p]
config = b"".join(b"\\x00\\x00\\x00\\x01" + p for p in parts if p[0] & 31 in (7, 8))
idr = b"".join(b"\\x00\\x00\\x00\\x01" + p for p in parts if p[0] & 31 == 5)
for _ in range(20):
    result = m.create_decoder(config)
    assert result is not None
    w, h, handle = result
    assert (w, h) == (64, 64)
    assert m.enqueue_frame(handle, idr, 0)
    m.destroy_decoder(handle)
    m.destroy_decoder(handle)
    assert m.get_current_frame_bgra8(handle) is None
    assert m.enqueue_frame(handle, idr, 0) is False
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(h264)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_independent_handles_shared_bytes_and_gc(h264):
    env = os.environ.copy()
    env.pop("PYTHON_GIL", None)
    result = subprocess.run(
        [
            sys.executable,
            "-Werror::RuntimeWarning",
            str(Path(__file__).with_name("native_thread_stress.py")),
            str(h264),
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cancelled_frame_read_waits_for_native_worker(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def read(handle):
        entered.set()
        release.wait(3)
        return (1, 1, b"\x00" * 4)

    monkeypatch.setattr(vtb_decoder, "get_current_frame_bgra8", read)

    async def run():
        decoder = object.__new__(vtb_decoder.VtbH264Decoder)
        decoder.valid = True
        decoder.handle = object()
        task = asyncio.create_task(decoder.get_current_frame_bgra8())
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done(), (
                "cancellation released the caller while native work remained"
            )
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())


def test_destroy_drains_previously_submitted_gcd_work(h264, tmp_path):
    root = Path(__file__).resolve().parents[1]
    parts = [p for p in re.split(b"\x00\x00\x00?\x01", h264.read_bytes()) if p]
    config = b"".join(b"\x00\x00\x00\x01" + p for p in parts if p[0] & 31 in (7, 8))
    source = tmp_path / "drain.c"
    source.write_text(
        """
#include "vtb_decoder.c"
#include <unistd.h>
int main(void) {
  uint8_t config[] = {CONFIG};
  void *decoder = NULL;
  int32_t width = 0, height = 0;
  if (vtb_create_decoder(config, sizeof(config), &decoder, &width, &height)) {
    return 2;
  }
  vtb_decoder_t *inner = decoder;
  dispatch_semaphore_t gate = dispatch_semaphore_create(0);
  __block int completed = 0;
  dispatch_async(inner->queue, ^{
    dispatch_semaphore_wait(gate, DISPATCH_TIME_FOREVER);
    completed = 1;
  });
  dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 50000000),
                 dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
    dispatch_semaphore_signal(gate);
  });
  vtb_destroy_decoder(&decoder);
  int result = completed ? 0 : 3;
  // Keep the deliberately delayed block alive even if a regression returns early.
  usleep(100000);
  dispatch_release(gate);
  return result;
}
""".replace("CONFIG", ",".join(map(str, config)))
    )
    binary = tmp_path / "drain"
    native = root / "native_code/macOS"
    subprocess.run(
        [
            "clang",
            "-fblocks",
            "-I" + str(native / "include"),
            "-I" + str(native / "src"),
            str(source),
            str(native / "src/vtb_helper.c"),
            "-framework",
            "CoreFoundation",
            "-framework",
            "CoreVideo",
            "-framework",
            "CoreMedia",
            "-framework",
            "VideoToolbox",
            "-framework",
            "Accelerate",
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    result = subprocess.run([str(binary)], timeout=10)
    assert result.returncode == 0, "destroy returned while GCD work was still pending"


def test_concurrent_native_reads_enqueue_and_close(h264):
    script = r"""
import concurrent.futures, re, sys, time
from pathlib import Path
from adb_scr.media_ext import _adb_scr_media as m
parts = [p for p in re.split(b"\x00\x00\x00?\x01", Path(sys.argv[1]).read_bytes()) if p]
config = b"".join(b"\0\0\0\1" + p for p in parts if p[0] & 31 in (7, 8))
idr = b"".join(b"\0\0\0\1" + p for p in parts if p[0] & 31 == 5)
with concurrent.futures.ThreadPoolExecutor(4) as pool:
    for _ in range(20):
        w, h, handle = m.create_decoder(config)
        assert m.enqueue_frame(handle, idr, 0)
        deadline = time.monotonic() + 2
        frame = None
        while frame is None and time.monotonic() < deadline:
            frame = m.get_current_frame_bgra8(handle)
            time.sleep(0.001)
        assert frame is not None
        assert frame[:2] == (64, 64) and len(frame[2]) == 16384
        jpeg = m.bgra8_to_jpg(*frame, 75)
        assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
        futures = []
        for i in range(12):
            futures.append(pool.submit(m.get_current_frame_bgra8, handle))
            futures.append(pool.submit(m.get_current_frame_jpg, handle, 75,
                                       0.5 if i % 2 else 1.0))
            futures.append(pool.submit(m.enqueue_frame, handle, idr, i))
        futures.append(pool.submit(m.destroy_decoder, handle))
        futures.append(pool.submit(m.destroy_decoder, handle))
        for future in futures:
            future.result()
        assert m.get_current_frame_bgra8(handle) is None
        assert m.get_current_frame_jpg(handle) is None
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(h264)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
