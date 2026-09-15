"""Validate the installed Windows wheel, system codecs and Python ABI; no ADB."""

import os
import sys
import sysconfig
from pathlib import Path

from check_ci import check_archives, check_gil, check_imports, check_sources

TESTS = [
    "tests/test_windows_media.py", "tests/test_windows_media_errors.py",
    "tests/test_device_session.py", "tests/test_recording.py",
    "tests/test_lifecycle.py", "tests/test_action_series.py", "tests/test_audio_stream.py",
    "tests/test_jpg.py", "tests/test_free_threading.py",
    "tests/test_native_lifecycle.py::test_cancelled_frame_read_waits_for_native_worker",
    "tests/test_recording_native.py::test_native_recording_rejects_invalid_quality_before_file_creation",
]


def main() -> int:
    os.chdir(Path(__file__).resolve().parents[2])
    expected = sys.argv[1]
    assert sys.platform == "win32"
    assert sys.version_info[:2] == tuple(map(int, expected.rstrip("t").split(".")))
    free_threaded = expected.endswith("t")
    assert bool(sysconfig.get_config_var("Py_GIL_DISABLED")) == free_threaded
    check_gil(free_threaded)
    check_sources()
    check_archives(free_threaded)
    check_imports(include_mcp="--core-only" not in sys.argv)
    import pytest
    check_gil(free_threaded)
    result = pytest.main([*TESTS, "-q", "-ra", "-o", "log_cli=false",
                          "--junitxml=test-results.xml", "-Werror::RuntimeWarning"])
    check_gil(free_threaded)
    if result:
        return int(result)
    result = pytest.main(["tests/test_run.py", "--collect-only", "-q"])
    check_gil(free_threaded)
    print(f"Windows validation completed: {sys.version}; GIL disabled={free_threaded}", flush=True)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
