"""Subprocess workload for native concurrency; accepts synthetic H.264 only."""

import concurrent.futures
import gc
import re
import sys
import sysconfig
import threading
import time
from pathlib import Path


def check_gil():
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        assert not sys._is_gil_enabled(), "native workload enabled the GIL"


def main():
    check_gil()
    from adb_scr.media_ext import _adb_scr_media as media

    check_gil()
    parts = [p for p in re.split(b"\0\0\0?\1", Path(sys.argv[1]).read_bytes()) if p]
    config = b"".join(b"\0\0\0\1" + p for p in parts if p[0] & 31 in (7, 8))
    idr = b"".join(b"\0\0\0\1" + p for p in parts if p[0] & 31 == 5)
    # All workers retain and read the same immutable Python input objects.
    bgra = bytes(range(256)) * 4096
    start = threading.Barrier(4, timeout=10)
    stop = threading.Event()

    def collect():
        while not stop.is_set():
            gc.collect()
            stop.wait(0.002)

    def check_jpeg(jpeg):
        assert jpeg is not None
        assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")

    def run(_):
        start.wait()
        for _ in range(40):
            check_jpeg(media.bgra8_to_jpg(512, 512, bgra, 75))
        for cycle in range(12):
            width, height, handle = media.create_decoder(config)
            assert (width, height) == (64, 64)
            try:
                assert media.enqueue_frame(handle, idr, cycle)
                deadline = time.monotonic() + 5
                frame = None
                while frame is None:
                    frame = media.get_current_frame_bgra8(handle)
                    assert time.monotonic() < deadline, (
                        "decoder did not produce a frame"
                    )
                    if frame is None:
                        time.sleep(0.001)
                assert frame[:2] == (width, height)
                assert len(frame[2]) == width * height * 4
                check_jpeg(media.get_current_frame_jpg(handle))
                check_jpeg(media.get_current_frame_jpg(handle, 85, 0.5, (8, 8, 32, 32)))
            finally:
                if cycle % 2:
                    media.destroy_decoder(handle)
                    media.destroy_decoder(handle)
                    assert media.get_current_frame_jpg(handle) is None
                # Other cycles rely on Capsule finalization while GC is active.
                del handle
        check_gil()
        return 12

    collector = threading.Thread(target=collect)
    collector.start()
    try:
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            assert sum(pool.map(run, range(4))) == 48
    finally:
        stop.set()
        collector.join()
    gc.collect()
    check_gil()


if __name__ == "__main__":
    main()
