"""Verify native thread-state contracts without devices or timing thresholds."""

import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest


def run_python(script, *args):
    # Forcing PYTHON_GIL=0 would hide a missing extension declaration.
    env = os.environ.copy()
    env.pop("PYTHON_GIL", None)
    result = subprocess.run(
        [sys.executable, "-Werror::RuntimeWarning", "-c", script, *map(str, args)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not sysconfig.get_config_var("Py_GIL_DISABLED"),
    reason="requires a free-threaded CPython build",
)
def test_import_preserves_disabled_gil():
    run_python("""
import sys
assert not sys._is_gil_enabled(), "interpreter did not start with GIL disabled"
import adb_scr
from adb_scr.media_ext import _adb_scr_media
assert not sys._is_gil_enabled(), "import enabled the GIL"
""")


@pytest.fixture(scope="module")
def thread_state_probe(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    directory = tmp_path_factory.mktemp("thread-state")
    # Build a test-only variant through the project's setup.py configuration.
    # Only the binding translation unit is wrapped; all native work stays real.
    script = """
import runpy
import sys
from pathlib import Path
import setuptools

root, directory = map(Path, sys.argv[1:])
original_setup = setuptools.setup

def setup_probe(**kwargs):
    extension, = kwargs["ext_modules"]
    extension.name = "_adb_scr_media"
    extension.sources = [
        str(root / "tests/native_thread_state.c")
        if path.endswith("/adb_scr_media.c") else str(root / path)
        for path in extension.sources if not path.endswith("/jpg_encoder.c")
    ]
    extension.include_dirs = [str(root / path) for path in extension.include_dirs]
    extension.include_dirs.append(str(root / "native_code/macOS/src"))
    original_setup(
        name="thread-state-probe", version="0", ext_modules=[extension],
        script_args=["build_ext", "--build-lib", str(directory),
                     "--build-temp", str(directory / "build")],
    )

setuptools.setup = setup_probe
runpy.run_path(str(root / "setup.py"), run_name="__main__")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(root), str(directory)],
        cwd=directory,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return directory


def test_bgra_encoder_detaches_thread_state(thread_state_probe):
    run_python(
        """
import sys
sys.path.insert(0, sys.argv[1])
import _adb_scr_media as media
pixels = bytes((20, 40, 80, 255)) * (64 * 64)
for _ in range(3):
    jpeg = media.bgra8_to_jpg(64, 64, pixels, 75)
    assert jpeg is not None, "native encoder entered with Python thread state attached"
    assert jpeg.startswith(b"\\xff\\xd8") and jpeg.endswith(b"\\xff\\xd9")
    # Returning valid Python objects also exercises thread-state restoration.
    assert pixels[:4] == bytes((20, 40, 80, 255))
""",
        thread_state_probe,
    )
