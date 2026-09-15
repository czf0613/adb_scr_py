"""Build an isolated test variant through setup.py; invoke with uv run."""

import runpy
import sys
from pathlib import Path

import setuptools

root = Path(__file__).resolve().parents[1]
directory = Path(sys.argv[1]).resolve()
original_setup = setuptools.setup


def setup_probe(**kwargs):
    extension, = kwargs["ext_modules"]
    extension.name = "_adb_scr_media"
    extension.sources = [str(root / path) for path in extension.sources]
    extension.include_dirs = [str(root / path) for path in extension.include_dirs]
    extension.include_dirs.append(str(root / "tests"))
    extension.define_macros.append(("ADB_SCR_TESTING", "1"))
    if "--cpu" in sys.argv[2:]:
        extension.define_macros.append(("ADB_SCR_TEST_DISABLE_D3D", "1"))
    extension.depends = [str(root / "tests/windows_probe.h"), str(root / "native_code/Windows/include/media.h")]
    original_setup(
        name="windows-media-probe", version="0", ext_modules=[extension],
        script_args=["build_ext", "--build-lib", str(directory),
                     "--build-temp", str(directory / "build")],
    )


setuptools.setup = setup_probe
runpy.run_path(str(root / "setup.py"), run_name="__main__")
