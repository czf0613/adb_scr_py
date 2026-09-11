"""Exercise real device/session orchestration with synthetic subprocesses and streams."""

import asyncio
import sys
from contextlib import suppress
from unittest.mock import AsyncMock

import pytest

from adb_scr.device import android_device as module


def install_device_fakes(monkeypatch, *, fail_control=False, process_exit=False):
    from adb_scr.device import control_handle

    processes = []
    peers = []
    servers = []

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
            with suppress(ConnectionError):
                await reader.read()
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    tunnel_calls = 0

    async def tunnel(*args, **kwargs):
        nonlocal tunnel_calls
        tunnel_calls += 1
        if fail_control and tunnel_calls == 2:
            return None
        if not servers:
            servers.append(await asyncio.start_server(peer, "127.0.0.1", 0))
        return await asyncio.open_connection(
            "127.0.0.1", servers[0].sockets[0].getsockname()[1]
        )

    async def start(*args, **kwargs):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(0.15)"
            if process_exit
            else "import time; time.sleep(30)",
        )
        processes.append(process)
        return process

    monkeypatch.setattr(module, "push_file", AsyncMock(return_value=True))
    # These existing lifecycle cases exercise the video/control-only branch.
    monkeypatch.setattr(module, "adb_android_api_level", AsyncMock(return_value=29))
    monkeypatch.setattr(module, "start_scrcpy_server", start)
    monkeypatch.setattr(control_handle, "setup_tunnel", tunnel)

    async def cleanup():
        for server in servers:
            server.close()
            await server.wait_closed()
        for writer in peers:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
        for process in processes:
            if process.returncode is None:
                process.kill()
            await process.wait()

    return processes, peers, cleanup


def test_partial_connect_rolls_back_process(monkeypatch):
    async def run():
        processes, _, cleanup = install_device_fakes(monkeypatch, fail_control=True)
        device = module.AndroidDevice("synthetic", "usb")
        try:
            assert await device.connect() is False
            assert processes[0].returncode is not None
            assert device.scrcpy_server_process is None
            assert device.control_handle is None
        finally:
            await device.disconnect()
            await cleanup()

    asyncio.run(run())


def test_process_exit_notifies_and_same_device_reconnects(monkeypatch):
    async def run():
        processes, _, cleanup = install_device_fakes(monkeypatch, process_exit=True)
        device = module.AndroidDevice("synthetic", "usb")
        try:
            assert await device.connect()
            assert device.is_connected
            assert await asyncio.wait_for(device.wait_disconnected(), 2)
            assert not device.is_connected
            assert device.control_handle is None
            assert await device.connect()
            assert len(processes) == 2
        finally:
            await device.disconnect()
            await cleanup()

    asyncio.run(run())


def test_cancel_connect_reaps_owned_process(monkeypatch):
    async def run():
        processes, _, cleanup = install_device_fakes(monkeypatch)
        entered = asyncio.Event()
        from adb_scr.device import control_handle

        async def tunnel(*args, **kwargs):
            entered.set()
            await asyncio.Future()

        monkeypatch.setattr(control_handle, "setup_tunnel", tunnel)
        device = module.AndroidDevice("synthetic", "usb")
        task = asyncio.create_task(device.connect())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert processes[0].returncode is not None
            assert device.scrcpy_server_process is None
        finally:
            await device.disconnect()
            await cleanup()

    asyncio.run(run())


def test_probe_failure_threshold_and_success_reset(monkeypatch):
    from adb_scr import ConnectionOptions

    async def run():
        _, _, cleanup = install_device_fakes(monkeypatch)
        results = iter([False, True, False, False])
        calls = []

        async def probe(serial, cmd, *args, **kwargs):
            calls.append((serial, cmd, args))
            return next(results)

        monkeypatch.setattr(module, "adb_device_cmd", probe)
        device = module.AndroidDevice(
            "synthetic",
            "usb",
            options=ConnectionOptions(probe_interval=0.005, probe_failures=2),
        )
        try:
            assert await device.connect()
            reason = await asyncio.wait_for(device.wait_disconnected(), 1)
            assert "探测" in reason
            assert len(calls) == 4
            assert all(call == ("synthetic", "shell", ("true",)) for call in calls)
            assert not device.is_connected
        finally:
            await device.disconnect()
            await cleanup()

    asyncio.run(run())


def test_old_disconnect_waiter_keeps_old_session_reason(monkeypatch):
    async def run():
        _, _, cleanup = install_device_fakes(monkeypatch)
        device = module.AndroidDevice("synthetic", "usb")
        try:
            assert await device.connect()
            waiter = asyncio.create_task(device.wait_disconnected())
            await asyncio.sleep(0)
            await device.disconnect()
            assert await device.connect()
            assert await waiter == "主动断开"
            assert device.is_connected
        finally:
            await device.disconnect()
            await cleanup()

    asyncio.run(run())


def test_disconnect_notification_waits_for_probe_cleanup(monkeypatch):
    from adb_scr import ConnectionOptions

    async def run():
        processes, _, cleanup = install_device_fakes(monkeypatch)
        entered = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()

        async def probe(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()

        monkeypatch.setattr(module, "adb_device_cmd", probe)
        device = module.AndroidDevice(
            "synthetic", "usb", options=ConnectionOptions(probe_interval=0.001)
        )
        try:
            assert await device.connect()
            await entered.wait()
            waiter = asyncio.create_task(device.wait_disconnected())
            processes[0].kill()
            await asyncio.wait_for(cleaning.wait(), 1)
            await asyncio.sleep(0)
            assert not waiter.done(), "notification preceded cleanup of the probe task"
            release.set()
            await asyncio.wait_for(waiter, 1)
        finally:
            release.set()
            await device.disconnect()
            await cleanup()

    asyncio.run(run())


def test_eof_interrupts_long_press_and_reclaims_session(monkeypatch):
    async def run():
        processes, peers, cleanup = install_device_fakes(monkeypatch)
        device = module.AndroidDevice("synthetic", "usb")
        try:
            assert await device.connect()
            press = asyncio.create_task(device.long_press(10, 10, 30000))
            await asyncio.sleep(0.02)
            peers[1].close()
            await peers[1].wait_closed()
            await asyncio.wait_for(device.wait_disconnected(), 0.3)
            await asyncio.wait_for(press, 0.3)
            assert processes[0].returncode is not None
        finally:
            if "press" in locals() and not press.done():
                press.cancel()
                await asyncio.gather(press, return_exceptions=True)
            await device.disconnect()
            await cleanup()

    asyncio.run(run())


def test_disconnect_during_tunnel_setup_cleans_late_socket(monkeypatch):
    from adb_scr import ConnectionOptions
    from adb_scr.device import control_handle

    async def run():
        _, _, cleanup = install_device_fakes(monkeypatch)
        original_tunnel = control_handle.setup_tunnel
        pending = asyncio.Event()
        release = asyncio.Event()
        sockets = []

        async def tunnel(*args, **kwargs):
            if sockets:
                pending.set()
                await release.wait()
            result = await original_tunnel(*args, **kwargs)
            sockets.append(result)
            return result

        monkeypatch.setattr(control_handle, "setup_tunnel", tunnel)
        device = module.AndroidDevice(
            "synthetic", "usb", options=ConnectionOptions(io_timeout=0.1)
        )
        connect = asyncio.create_task(device.connect())
        disconnect = None
        try:
            await asyncio.wait_for(pending.wait(), 1)
            handle = device.control_handle
            disconnect = asyncio.create_task(device.disconnect())
            # Give an erroneous premature cleanup time to complete deterministically.
            await asyncio.sleep(0.02)
            release.set()
            await asyncio.wait_for(connect, 1)
            await asyncio.wait_for(disconnect, 1)
            assert all(writer.is_closing() for _, writer in sockets)
            assert handle.video_socket_writer is None
            assert handle.control_socket_writer is None
            assert not handle.async_tasks
        finally:
            release.set()
            for task in (connect, disconnect):
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await device.disconnect()
            for _, writer in sockets:
                writer.close()
                with suppress(ConnectionError):
                    await writer.wait_closed()
            await cleanup()

    asyncio.run(run())
