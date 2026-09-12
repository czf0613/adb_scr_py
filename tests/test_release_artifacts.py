"""Release bundles must contain exactly the tested macOS arm64 distributions."""

import importlib.util
import io
import tarfile
import zipfile
from itertools import product
from pathlib import Path

import pytest


def load_prepare_release():
    path = Path(__file__).resolve().parents[1] / ".github/scripts/prepare_release.py"
    spec = importlib.util.spec_from_file_location("prepare_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prepare_release


@pytest.fixture
def artifacts(tmp_path):
    root = tmp_path / "artifacts"
    abis = [("310", "310"), ("311", "311"), ("312", "312"),
            ("313", "313"), ("314", "314"), ("314", "314t")]
    for macos, (python, abi) in product((15, 26), abis):
        directory = root / f"wheel-macos-{macos}-{abi}"
        directory.mkdir(parents=True)
        tag = f"cp{python}-cp{abi}-macosx_{macos}_0_arm64"
        metadata = b"Metadata-Version: 2.4\nName: adb_scr_py\nVersion: 0.3.0\n"
        wheel = directory / f"adb_scr_py-0.3.0-{tag}.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("adb_scr_py-0.3.0.dist-info/METADATA", metadata)
            archive.writestr("adb_scr_py-0.3.0.dist-info/WHEEL", f"Tag: {tag}\n")
        with tarfile.open(directory / "adb_scr_py-0.3.0.tar.gz", "w:gz") as archive:
            member = tarfile.TarInfo("adb_scr_py-0.3.0/PKG-INFO")
            member.size = len(metadata)
            archive.addfile(member, io.BytesIO(metadata))
    return root


def test_complete_bundle_contains_twelve_wheels_and_one_sdist(artifacts, tmp_path):
    output = tmp_path / "dist"
    load_prepare_release()(artifacts, output, "0.3.0", "v0.3.0")
    assert len(list(output.glob("*.whl"))) == 12
    assert len(list(output.glob("*.tar.gz"))) == 1
    for path in output.iterdir():
        assert path.read_bytes() == next(artifacts.rglob(path.name)).read_bytes()


@pytest.mark.parametrize("macos", [15, 26])
def test_missing_macos_target_is_rejected(artifacts, tmp_path, macos):
    for wheel in artifacts.rglob(f"*macosx_{macos}_0_arm64.whl"):
        wheel.unlink()
    output = tmp_path / "dist"
    with pytest.raises(ValueError, match="wheel set"):
        load_prepare_release()(artifacts, output, "0.3.0")
    assert not output.exists()


@pytest.mark.parametrize("problem", ["missing", "x86_64", "duplicate", "version"])
def test_invalid_wheel_set_never_creates_output(artifacts, tmp_path, problem):
    wheel = next(artifacts.rglob("*cp310*.whl"))
    if problem == "missing":
        wheel.unlink()
    elif problem == "duplicate":
        (artifacts / wheel.name).write_bytes(wheel.read_bytes())
    else:
        name = wheel.name.replace("arm64", "x86_64") if problem == "x86_64" else (
            wheel.name.replace("0.3.0", "0.3.1")
        )
        wheel.rename(wheel.with_name(name))
    output = tmp_path / "dist"
    with pytest.raises(ValueError, match="wheel set"):
        load_prepare_release()(artifacts, output, "0.3.0")
    assert not output.exists()


def test_mismatched_release_tag_never_creates_output(artifacts, tmp_path):
    output = tmp_path / "dist"
    with pytest.raises(ValueError, match="tag"):
        load_prepare_release()(artifacts, output, "0.3.0", "v0.3.1")
    assert not output.exists()


@pytest.mark.parametrize("metadata_file,content", [
    ("METADATA", "Name: adb_scr_py\nVersion: 0.3.1\n"),
    ("WHEEL", "Tag: cp310-cp310-macosx_15_0_x86_64\n"),
])
def test_incorrect_wheel_metadata_is_rejected(artifacts, tmp_path, metadata_file, content):
    wheel = next(artifacts.rglob("*cp310*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members[f"adb_scr_py-0.3.0.dist-info/{metadata_file}"] = content.encode()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    output = tmp_path / "dist"
    with pytest.raises(ValueError, match="metadata"):
        load_prepare_release()(artifacts, output, "0.3.0")
    assert not output.exists()


def test_missing_sdist_is_rejected(artifacts, tmp_path):
    next(artifacts.rglob("*.tar.gz")).unlink()
    with pytest.raises(ValueError, match="sdist"):
        load_prepare_release()(artifacts, tmp_path / "dist", "0.3.0")


def test_existing_output_is_preserved(artifacts, tmp_path):
    output = tmp_path / "dist"
    output.mkdir()
    marker = output / "existing.whl"
    marker.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="empty"):
        load_prepare_release()(artifacts, output, "0.3.0")
    assert list(output.iterdir()) == [marker]
    assert marker.read_bytes() == b"preserve"
