"""Synthetic ADB outputs and scrcpy audio framing; never contacts a device."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adb_scr.adb_cmd import base, device_control
from adb_scr.device import android_device, control_handle
from adb_scr.device.options import ConnectionOptions


def packet(payload, pts=0, *, config=False):
    flags = (1 << 63) if config else pts
    return flags.to_bytes(8, "big") + len(payload).to_bytes(4, "big") + payload


@pytest.mark.parametrize("output, expected", [(b"33\n", 33), (b" 30\r\n", 30), (b"36", 36)])
def test_sdk_query_captures_and_parses_device_output(monkeypatch, output, expected):
    calls = []

    async def run(*args, **kwargs):
        calls.append((args, kwargs))
        return 0, output, b""

    monkeypatch.setattr(base, "_run", run)
    assert hasattr(base, "adb_android_api_level"), "SDK query not implemented"
    assert asyncio.run(base.adb_android_api_level("synthetic", timeout=0.1)) == expected
    assert calls == [(("-s", "synthetic", "shell", "getprop", "ro.build.version.sdk"),
                      {"capture": True, "timeout": 0.1})]


@pytest.mark.parametrize("code, output", [(0, b""), (0, b"unknown"), (0, b"-1"), (1, b"33"), (0, b"33\n34")])
def test_sdk_query_does_not_guess_on_invalid_or_failed_output(monkeypatch, code, output):
    monkeypatch.setattr(base, "_run", AsyncMock(return_value=(code, output, b"failed")))
    assert hasattr(base, "adb_android_api_level"), "SDK query not implemented"
    with pytest.raises(RuntimeError):
        asyncio.run(base.adb_android_api_level("synthetic"))


@pytest.mark.parametrize("sdk, enabled, source, duplicate", [
    (29, False, None, None), (30, True, "output", False),
    (32, True, "output", False), (33, True, "playback", True),
    (36, True, "playback", True),
])
def test_launcher_selects_audio_source(monkeypatch, sdk, enabled, source, duplicate):
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=None)

    monkeypatch.setattr(device_control.subprocess, "create_subprocess_exec", spawn)
    monkeypatch.setattr(device_control.asyncio, "sleep", AsyncMock())
    asyncio.run(device_control.start_scrcpy_server("synthetic", "00000001", android_api_level=sdk))
    args = calls[0]
    assert f"audio={str(enabled).lower()}" in args
    if enabled:
        assert "audio_codec=aac" in args
        assert f"audio_source={source}" in args
        assert f"audio_dup={str(duplicate).lower()}" in args


def test_sdk_failure_prevents_server_launch(monkeypatch):
    spawned = []
    monkeypatch.setattr(android_device, "adb_android_api_level", AsyncMock(side_effect=RuntimeError("bad SDK")), raising=False)
    monkeypatch.setattr(android_device, "push_file", AsyncMock(return_value=True))

    async def start(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("must not launch when SDK query fails")

    monkeypatch.setattr(android_device, "start_scrcpy_server", start)

    async def run():
        device = android_device.AndroidDevice("synthetic", "usb")
        assert not await device.connect()
        assert not spawned

    asyncio.run(run())


def test_audio_stream_preserves_payload_and_pts_during_silence():
    async def run():
        from adb_scr.device.audio_stream import AudioStream

        delivered = []

        async def consume(data, pts):
            delivered.append((data, pts))

        reader = asyncio.StreamReader()
        reader.feed_data(b"\0aac" + packet(b"\x11\x90", config=True) + packet(b"AAC", 1234567))
        stream = AudioStream(reader, 0.02, consume)
        task = asyncio.create_task(stream.run())
        try:
            await asyncio.wait_for(stream.ready.wait(), 1)
            await asyncio.sleep(0.04)
            assert stream.config == b"\x11\x90"
            assert delivered == [(b"AAC", 1234567)]
            assert not task.done(), "silence was treated as a timeout"
            reader.feed_eof()
            with pytest.raises(asyncio.IncompleteReadError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("codec, disabled", [(b"\0\0\0\0", True), (b"\0\0\0\1", False), (b"opus", False)])
def test_audio_disable_is_distinguished_from_protocol_failure(codec, disabled):
    async def run():
        from adb_scr.device.audio_stream import AudioStream

        reader = asyncio.StreamReader()
        reader.feed_data(codec)
        stream = AudioStream(reader, 0.02, AsyncMock())
        if disabled:
            await stream.run()
            assert stream.disabled
            assert stream.config is None
            assert stream.ready.is_set()
        else:
            with pytest.raises(RuntimeError):
                await stream.run()

    asyncio.run(run())


@pytest.mark.parametrize("payload", [b"\0", (0).to_bytes(8, "big") + (2 * 1024 * 1024).to_bytes(4, "big")])
def test_partial_or_oversized_audio_packet_fails(payload):
    async def run():
        from adb_scr.device.audio_stream import AudioStream

        reader = asyncio.StreamReader()
        reader.feed_data(b"\0aac" + packet(b"\x11\x90", config=True) + payload)
        stream = AudioStream(reader, 0.01, AsyncMock())
        with pytest.raises((asyncio.TimeoutError, ValueError)):
            await stream.run()

    asyncio.run(run())


@pytest.mark.parametrize("audio_header", [b"\0\0\0\0", b"\0aac" + packet(b"\x11\x90", config=True)])
def test_three_socket_order_and_cleanup(monkeypatch, audio_header):
    async def run():
        writers = []

        class Writer:
            closed = False

            def close(self):
                self.closed = True

            async def wait_closed(self):
                pass

        async def tunnel(*args, **kwargs):
            reader, writer = asyncio.StreamReader(), Writer()
            index = len(writers)
            writers.append(writer)
            if index == 0:
                reader.feed_data(b"\0" + b"synthetic".ljust(64, b"\0") + b"h264" + (64).to_bytes(4, "big") * 2)
            elif index == 1:
                reader.feed_data(audio_header)
            return reader, writer

        monkeypatch.setattr(control_handle, "setup_tunnel", tunnel)
        handle = control_handle.DeviceControlHandle("synthetic", "00000001", audio_enabled=True,
                                                    options=ConnectionOptions(io_timeout=0.05))
        try:
            assert await handle.connect_sockets()
            assert len(writers) == 3
            assert handle.audio_socket_writer is writers[1]
            assert handle.control_socket_writer is writers[2]
            assert handle.running
        finally:
            await handle.disconnect_sockets()
        assert all(writer.closed for writer in writers)

    asyncio.run(run())
