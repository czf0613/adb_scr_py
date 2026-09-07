"""Device-free regressions for stream EOF, lifecycle and configuration."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import adb_scr
from adb_scr import consts
from adb_scr.adb_cmd import device_control
from adb_scr.device.control_handle import DeviceControlHandle


def test_fps_updates_already_imported_launcher(monkeypatch):
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=None)

    monkeypatch.setattr(device_control.subprocess, "create_subprocess_exec", spawn)
    monkeypatch.setattr(device_control.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(consts, "SCREEN_FPS", 30)
    adb_scr.set_screen_record_fps(47)
    asyncio.run(device_control.start_scrcpy_server("synthetic", "00000001"))
    assert "max_fps=47" in calls[0]
    adb_scr.set_screen_record_fps(25.5)
    assert consts.SCREEN_FPS == 47
    assert "set_screen_record_fps" in adb_scr.__all__


def test_control_eof_stops_without_busy_loop():
    async def run():
        class BoundedReader(asyncio.StreamReader):
            reads = 0

            async def read(self, n=-1):
                self.reads += 1
                if self.reads > 10:
                    raise RuntimeError("EOF loop safety stop")
                return await super().read(n)

        reader = BoundedReader()
        reader.feed_eof()
        handle = DeviceControlHandle("synthetic", "00000001")
        handle.control_socket_reader = reader
        handle.running = True
        await handle.process_control_upstream()
        assert reader.reads == 1
        await handle.disconnect_sockets()

    asyncio.run(run())


def test_control_messages_are_consumed_before_eof():
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(b"clipboard")
        handle = DeviceControlHandle("synthetic", "00000001")
        handle.control_socket_reader = reader
        handle.running = True
        task = asyncio.create_task(handle.process_control_upstream())
        await asyncio.sleep(0)
        # Bounded read returns after consuming available data, then waits again.
        assert reader._buffer == b""
        reader.feed_eof()
        await asyncio.wait_for(task, 1)
        await handle.disconnect_sockets()

    asyncio.run(run())


def test_tunnel_handshake_timeout_closes_transport(monkeypatch):
    from adb_scr.device import tcp_forward_tunnel as tunnel

    async def run():
        disconnected = asyncio.Event()

        async def peer(reader, writer):
            try:
                while await reader.read(4096):
                    pass
            finally:
                writer.close()
                await writer.wait_closed()
                disconnected.set()

        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        real_open = asyncio.open_connection

        async def open_fake_adb(*args, **kwargs):
            return await real_open("127.0.0.1", server.sockets[0].getsockname()[1])

        monkeypatch.setattr(tunnel.asyncio, "open_connection", open_fake_adb)
        try:
            assert await tunnel.setup_tunnel("synthetic", "uds", timeout=0.03) is None
            await asyncio.wait_for(disconnected.wait(), 1)
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_session_eof_cleans_both_streams(monkeypatch):
    from adb_scr.device import control_handle as module

    async def run():
        peers = []

        async def peer(reader, writer):
            peers.append(writer)
            if len(peers) % 2:
                writer.write(
                    b"\0"
                    + b"synthetic".ljust(64, b"\0")
                    + b"h264"
                    + (64).to_bytes(4, "big") * 2
                )
                await writer.drain()
            try:
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(peer, "127.0.0.1", 0)

        async def tunnel(*args, **kwargs):
            return await asyncio.open_connection(
                "127.0.0.1", server.sockets[0].getsockname()[1]
            )

        monkeypatch.setattr(module, "setup_tunnel", tunnel)
        handle = DeviceControlHandle("synthetic", "00000001")
        try:
            assert await handle.connect_sockets()
            assert (handle.screen_width, handle.screen_height) == (64, 64)
            # A static screen must remain connected without any video packets.
            await asyncio.sleep(0.04)
            assert handle.running
            peers[1].close()
            await peers[1].wait_closed()
            reason = await asyncio.wait_for(handle.wait_disconnected(), 1)
            assert reason
            assert not handle.running
            assert handle.video_socket_writer is None
            assert handle.control_socket_writer is None
            assert handle.h264_decoder is None
        finally:
            await handle.disconnect_sockets()
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_partial_video_packet_times_out_and_closes(monkeypatch):
    from adb_scr import ConnectionOptions

    async def run():
        handle = DeviceControlHandle(
            "synthetic", "00000001", options=ConnectionOptions(io_timeout=0.02)
        )
        reader = asyncio.StreamReader()
        reader.feed_data(
            b"\0"
            + b"synthetic".ljust(64, b"\0")
            + b"h264"
            + (64).to_bytes(4, "big") * 2
            + b"\x00"
        )
        handle.video_socket_reader = reader
        handle.running = True
        task = asyncio.create_task(handle.process_video_upstream())
        handle.async_tasks = [task]
        reason = await asyncio.wait_for(handle.wait_disconnected(), 1)
        assert "TimeoutError" in reason
        assert not handle.running
        assert handle.video_socket_reader is None
        assert task.done()

    asyncio.run(run())


def test_adb_command_timeout_and_cancel_reap_child(monkeypatch):
    import sys

    import pytest

    from adb_scr.adb_cmd import base

    async def run(cancel):
        children = []
        entered = asyncio.Event()
        real_spawn = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            child = await real_spawn(
                sys.executable, "-c", "import time; time.sleep(30)", **kwargs
            )
            children.append(child)
            entered.set()
            return child

        monkeypatch.setattr(base.subprocess, "create_subprocess_exec", spawn)
        task = asyncio.create_task(
            base._run("synthetic", timeout=1 if cancel else 0.03)
        )
        await entered.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
            await task
        assert children[0].returncode is not None
        monkeypatch.setattr(base.subprocess, "create_subprocess_exec", real_spawn)

    asyncio.run(run(False))
    asyncio.run(run(True))


def test_stop_process_drains_paused_output_pipe():
    import sys

    from adb_scr.async_utils import stop_process

    async def run():
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import os,time; os.write(1,b'x'*1000000); time.sleep(30)",
            stdout=asyncio.subprocess.PIPE,
        )
        try:
            deadline = asyncio.get_running_loop().time() + 1
            while (
                not child.stdout._paused
                and asyncio.get_running_loop().time() < deadline
            ):
                await asyncio.sleep(0.005)
            assert child.stdout._paused
            await stop_process(child, timeout=0.1)
            assert child.returncode is not None
            assert child.stdout.at_eof()
        finally:
            if child.returncode is None:
                child.kill()
            await child.communicate()

    asyncio.run(run())
