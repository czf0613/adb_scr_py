"""File/bytes transfers through synthetic ADB children; never touch a phone."""

import asyncio
import sys
import threading
from pathlib import Path

import pytest

from adb_scr import AndroidDevice
from adb_scr.adb_cmd import base, device_control


@pytest.fixture
def fake_transfer(monkeypatch, tmp_path):
    phone = tmp_path / "phone"
    phone.mkdir()
    calls = []
    children = []
    spawn = asyncio.create_subprocess_exec
    script = """
import pathlib, shutil, sys
root, selector, serial, command, source, target = sys.argv[1:]
assert selector == '-s' and serial == 'synthetic'
if command == 'push':
    target = pathlib.Path(root) / target.lstrip('/')
elif command == 'pull':
    source = pathlib.Path(root) / source.lstrip('/')
else:
    raise AssertionError(command)
shutil.copyfile(source, target)
"""

    async def adb(*args, **kwargs):
        calls.append(args[1:])
        child = await spawn(sys.executable, "-c", script, str(phone), *args[1:], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(base.subprocess, "create_subprocess_exec", adb)
    return phone, calls, children


@pytest.mark.parametrize("payload", [b"", b"\x00\xff\xfe\r\nhello\n" * 100])
def test_file_and_bytes_roundtrip_without_media_session(fake_transfer, tmp_path, payload):
    phone, calls, children = fake_transfer
    # Shell metacharacters must remain literal in both local and remote paths.
    name = "文件 with spaces; $(echo nope).bin"
    source = tmp_path / name
    source.write_bytes(payload)
    output = tmp_path / "download.bin"

    async def run():
        device = AndroidDevice("synthetic", "usb")
        assert not device.is_connected
        assert await device.push_file(source, "/" + name)
        assert (phone / name).read_bytes() == payload
        assert await device.pull_file("/" + name, output)
        assert output.read_bytes() == payload
        assert await device.push_file(payload, "/bytes.bin")
        assert await device.pull_file("/bytes.bin") == payload
        assert not device.is_connected

    asyncio.run(run())
    assert len(calls) == 4
    assert calls[0] == ("-s", "synthetic", "push", str(source), "/" + name)
    assert not Path(calls[2][3]).parent.exists()
    assert not Path(calls[3][4]).parent.exists()
    assert all(child.returncode == 0 for child in children)


def test_transfer_failure_and_missing_files(fake_transfer, tmp_path):
    _, calls, children = fake_transfer

    async def run():
        device = AndroidDevice("synthetic", "usb")
        assert await device.push_file(tmp_path / "missing", "/target") is False
        assert calls == []
        assert await device.push_file(b"data", "/missing/target") is False
        assert not Path(calls[-1][3]).parent.exists()
        assert await device.pull_file("/missing") is None
        assert not Path(calls[-1][4]).parent.exists()
        assert await device.pull_file("/missing", tmp_path / "output") is False

    asyncio.run(run())
    assert all(child.returncode != 0 for child in children)


@pytest.mark.parametrize("method", ["push_file", "pull_file"])
@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "60"])
def test_invalid_transfer_timeout_does_not_spawn(fake_transfer, method, timeout):
    _, calls, _ = fake_transfer

    async def run():
        device = AndroidDevice("synthetic", "usb")
        args = (b"data", "/target") if method == "push_file" else ("/target",)
        with pytest.raises((TypeError, ValueError)):
            await getattr(device, method)(*args, timeout=timeout)

    asyncio.run(run())
    assert calls == []


@pytest.mark.parametrize("remote", ["", "relative/file", "-option", "/bad\0file"])
def test_invalid_remote_path_does_not_spawn(fake_transfer, remote):
    _, calls, _ = fake_transfer

    async def run():
        device = AndroidDevice("synthetic", "usb")
        with pytest.raises(ValueError):
            await device.push_file(b"data", remote)
        with pytest.raises(ValueError):
            await device.pull_file(remote)

    asyncio.run(run())
    assert calls == []


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("method", ["push_file", "pull_file"])
def test_timeout_and_cancellation_reap_child_before_removing_temp(monkeypatch, method, cancel):
    spawn = asyncio.create_subprocess_exec

    async def run():
        entered = asyncio.Event()
        children = []
        paths = []

        async def adb(*args, **kwargs):
            paths.append(Path(args[4] if method == "push_file" else args[5]))
            child = await spawn(sys.executable, "-c", "import time; time.sleep(30)", **kwargs)
            children.append(child)
            entered.set()
            return child

        monkeypatch.setattr(base.subprocess, "create_subprocess_exec", adb)
        device = AndroidDevice("synthetic", "usb")
        args = (b"data", "/target") if method == "push_file" else ("/target",)
        task = asyncio.create_task(getattr(device, method)(*args, timeout=5 if cancel else 0.1))
        await asyncio.wait_for(entered.wait(), 2)
        assert paths[0].parent.is_dir()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task is (False if method == "push_file" else None)
        assert children[0].returncode is not None
        assert not paths[0].parent.exists()

    asyncio.run(run())


def test_cancel_during_local_write_waits_before_temp_cleanup(fake_transfer, monkeypatch):
    _, calls, _ = fake_transfer
    entered = threading.Event()
    release = threading.Event()
    paths = []
    original = Path.write_bytes

    def write(path, data):
        paths.append(path)
        entered.set()
        assert release.wait(5)
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)

    async def run():
        task = asyncio.create_task(AndroidDevice("synthetic", "usb").push_file(b"data", "/target"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert paths[0].parent.is_dir()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not paths[0].parent.exists()
        assert calls == []

    asyncio.run(run())


def test_relative_local_names_cannot_become_adb_options(fake_transfer, monkeypatch, tmp_path):
    _, calls, _ = fake_transfer
    monkeypatch.chdir(tmp_path)
    Path("-file").write_bytes(b"data")
    assert asyncio.run(device_control.push_file("synthetic", "-file", "/target"))
    assert calls[0][3] == str(tmp_path / "-file")
