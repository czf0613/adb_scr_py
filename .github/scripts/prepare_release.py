"""Validate tested artifacts and assemble one complete PyPI upload directory."""

import argparse
import shutil
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path


def check_metadata(data: bytes, version: str, path: Path) -> None:
    metadata = BytesParser().parsebytes(data)
    name = metadata.get("Name", "").lower().replace("-", "_")
    if name != "adb_scr_py" or metadata.get("Version") != version:
        raise ValueError(f"Unexpected package metadata: {path}")


def prepare_release(artifacts: Path, output: Path, version: str,
                    release_tag: str = "") -> None:
    if release_tag and release_tag != f"v{version}":
        raise ValueError(f"Release tag {release_tag!r} must match v{version}")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"Output directory must be empty: {output}")

    tags = []
    for macos in (15, 26):
        tags.extend(f"cp{minor}-cp{minor}-macosx_{macos}_0_arm64"
                    for minor in range(310, 315))
        tags.append(f"cp314-cp314t-macosx_{macos}_0_arm64")
    expected = {f"adb_scr_py-{version}-{tag}.whl": tag for tag in tags}
    wheels = sorted(artifacts.rglob("*.whl"))
    if len(wheels) != len(expected) or {path.name for path in wheels} != set(expected):
        raise ValueError(f"Incomplete or unexpected wheel set: {[p.name for p in wheels]}")

    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            prefix = f"adb_scr_py-{version}.dist-info/"
            check_metadata(archive.read(prefix + "METADATA"), version, wheel)
            metadata = BytesParser().parsebytes(archive.read(prefix + "WHEEL"))
            if metadata.get_all("Tag") != [expected[wheel.name]]:
                raise ValueError(f"Unexpected wheel tag metadata: {wheel}")

    # Each matrix job uploads its own sdist. Publish just one, without overwriting
    # identically named files while downloading the independent artifacts.
    sdists = sorted(artifacts.rglob("*.tar.gz"))
    if len(sdists) != len(wheels) or any(
        path.name != f"adb_scr_py-{version}.tar.gz" for path in sdists
    ) or {path.parent for path in sdists} != {path.parent for path in wheels}:
        raise ValueError("Each wheel artifact must contain its matching sdist")
    for sdist in sdists:
        with tarfile.open(sdist) as archive:
            member = archive.extractfile(f"adb_scr_py-{version}/PKG-INFO")
            check_metadata(member.read(), version, sdist)

    output.mkdir(parents=True, exist_ok=True)
    for path in [*wheels, sdists[0]]:
        shutil.copyfile(path, output / path.name)
        print(path.name, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-tag", default="")
    args = parser.parse_args()
    prepare_release(args.artifacts, args.output, args.version, args.release_tag)


if __name__ == "__main__":
    main()
