"""Check the installed CI wheel with Python 3.10+; never connect to ADB."""

import importlib
import os
import sys
import sysconfig
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

# Explicit selection: the other media tests require real VideoToolbox hardware.
CI_TESTS = [
    "tests/test_action_series.py",
    "tests/test_audio_stream.py",
    "tests/test_device_session.py",
    "tests/test_lifecycle.py",
    "tests/test_recording.py",
    "tests/test_jpg.py",
    "tests/test_free_threading.py",
    "tests/test_release_artifacts.py",
    "tests/test_mcp_server.py",
    "tests/test_mcp_shutdown.py",
    "tests/test_native_lifecycle.py::test_cancelled_frame_read_waits_for_native_worker",
    "tests/test_recording_native.py::test_native_recording_api_exists",
]


def check_gil(free_threaded: bool) -> None:
    if free_threaded:
        assert "PYTHON_GIL" not in os.environ, "Do not force the GIL state"
        assert "gil" not in sys._xoptions, "Do not force the GIL state"
        assert not sys._is_gil_enabled(), "The GIL must remain disabled"


def check_sources() -> None:
    paths = [
        Path("setup.py"),
        *Path("src").rglob("*.py"),
        *Path("src").rglob("*.pyi"),
        *Path("tests").rglob("*.py"),
        *Path(".github/scripts").glob("*.py"),
    ]
    for path in paths:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    print(f"Compiled {len(paths)} Python source and stub files", flush=True)


def check_archives(free_threaded: bool) -> None:
    wheel, = Path("dist").glob("*.whl")
    sdist, = Path("dist").glob("*.tar.gz")
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    abi = tag + ("t" if free_threaded else "")
    assert f"-{tag}-{abi}-macosx_" in wheel.name, wheel.name

    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
    with tarfile.open(sdist) as archive:
        sdist_names = {
            name.split("/", 1)[1] for name in archive.getnames() if "/" in name
        }

    package_files = {
        path.as_posix()
        for package in ("adb_scr", "adb_scr_mcp")
        for path in (Path("src") / package).rglob("*")
        if path.is_file() and (
            path.suffix in {".py", ".pyi"}
            or path.name in {"py.typed", "scrcpy-server.bin", "agent_guide.md"}
        )
    }
    assert "src/adb_scr/res/scrcpy-server.bin" in package_files
    assert "src/adb_scr/py.typed" in package_files
    assert "src/adb_scr/media_ext/_adb_scr_media.pyi" in package_files
    assert "src/adb_scr_mcp/agent_guide.md" in package_files
    assert package_files <= sdist_names, package_files - sdist_names
    wheel_files = {name.removeprefix("src/") for name in package_files}
    assert wheel_files <= wheel_names, wheel_files - wheel_names
    extension = "adb_scr/media_ext/_adb_scr_media" + sysconfig.get_config_var("EXT_SUFFIX")
    assert extension in wheel_names, extension

    native_files = {
        path.as_posix()
        for directory in ("native_code/macOS/src", "native_code/macOS/include")
        for path in Path(directory).rglob("*")
        if path.is_file() and path.suffix in {".c", ".m", ".h"}
    }
    assert native_files <= sdist_names, native_files - sdist_names
    forbidden = {"docs", "tests", "AGENTS.md", "CLAUDE.md", ".github", ".superpowers"}
    for names in (wheel_names, sdist_names):
        assert not any(forbidden.intersection(PurePosixPath(name).parts) for name in names)
    print(f"Verified package contents and ABI: {sdist.name}, {wheel.name}", flush=True)


def check_imports() -> None:
    # Import every shipped module from site-packages, never from src/.
    site_packages = Path(sysconfig.get_path("platlib")).resolve()
    for path in sorted(Path("src").glob("adb_scr*/**/*.py")):
        parts = path.relative_to("src").with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        module = importlib.import_module(".".join(parts))
        assert Path(module.__file__).resolve().is_relative_to(site_packages), module.__file__
    from adb_scr.media_ext import _adb_scr_media

    assert Path(_adb_scr_media.__file__).resolve().is_relative_to(site_packages)
    from importlib.resources import files

    assert "get_agent_guide" in files("adb_scr_mcp").joinpath("agent_guide.md").read_text(encoding="utf-8")
    print(f"Imported all modules and the native extension from {site_packages}", flush=True)


def main() -> int:
    os.chdir(Path(__file__).resolve().parents[2])
    expected_version = sys.argv[1]
    free_threaded = expected_version.endswith("t")
    assert sys.version_info[:2] == tuple(map(int, expected_version.rstrip("t").split(".")))
    assert bool(sysconfig.get_config_var("Py_GIL_DISABLED")) == free_threaded
    print(f"Testing Python {sys.version}", flush=True)
    check_gil(free_threaded)
    check_sources()
    check_archives(free_threaded)
    check_imports()

    import pytest

    check_gil(free_threaded)
    result = pytest.main([*CI_TESTS, "-q", "-ra", "--junitxml=test-results.xml",
                          "-Werror::RuntimeWarning"])
    check_gil(free_threaded)
    if result:
        return int(result)
    # Collect only: executing this test starts ADB and can control a phone.
    result = pytest.main(["tests/test_run.py", "--collect-only", "-q",
                          "-Werror::RuntimeWarning"])
    check_gil(free_threaded)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
