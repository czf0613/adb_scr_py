import asyncio
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("mcp")
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def wait_until(condition, process, log):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if condition():
            return
        assert process.poll() is None, log.read_text()
        time.sleep(0.02)
    pytest.fail("Timed out waiting for process state:\n" + log.read_text())


@contextmanager
def child(tmp_path, mode="normal", port=None):
    if port is None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    event_file = tmp_path / "events.txt"
    log = tmp_path / "process.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("mcp_server_probe.py")),
                str(event_file),
                str(port),
                mode,
            ],
            stdout=output,
            stderr=output,
        )
    try:
        yield process, event_file, log, f"http://127.0.0.1:{port}"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def events(path):
    return path.read_text().splitlines() if path.exists() else []


def ready(url):
    try:
        return httpx.get(url + "/healthz", timeout=0.2).status_code == 200
    except httpx.TransportError:
        return False


def connect(url):
    async def run():
        async with (
            streamable_http_client(url + "/mcp") as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool("connect_device", {"serial": "phone"})
            return result

    return asyncio.run(run())


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_signal_and_repeated_signal_wait_for_async_cleanup(tmp_path, sig):
    with child(tmp_path) as (process, path, log, url):
        wait_until(lambda: ready(url), process, log)
        assert not connect(url).isError
        process.send_signal(sig)
        wait_until(lambda: "disconnect-start" in events(path), process, log)
        process.send_signal(signal.SIGINT)
        process.send_signal(signal.SIGTERM)
        time.sleep(0.05)
        assert process.poll() is None, log.read_text()
        assert "deinit-start" not in events(path)
        path.with_suffix(".cleanup-release").touch()
        assert process.wait(timeout=10) == 0, log.read_text()
        assert events(path)[-6:] == [
            "disconnect-start",
            "disconnect-end",
            "deinit-start",
            "deinit-end",
            "loop-still-running",
            "serve-returned",
        ]
        assert "Task was destroyed" not in log.read_text()


def test_signal_during_startup_still_runs_lifespan_shutdown(tmp_path):
    with child(tmp_path, "startup") as (process, path, log, _):
        wait_until(lambda: "init-start" in events(path), process, log)
        process.send_signal(signal.SIGINT)
        path.with_suffix(".init-release").touch()
        wait_until(lambda: "deinit-start" in events(path), process, log)
        process.send_signal(signal.SIGINT)
        path.with_suffix(".cleanup-release").touch()
        assert process.wait(timeout=10) == 0, log.read_text()
        assert events(path) == [
            "init-start",
            "init-end",
            "deinit-start",
            "deinit-end",
            "loop-still-running",
            "serve-returned",
        ]


def test_signal_cancels_inflight_connect_then_cleans_up(tmp_path):
    with child(tmp_path, "connecting") as (process, path, log, url):
        wait_until(lambda: ready(url), process, log)
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(
                httpx.post,
                url + "/mcp",
                timeout=5,
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "connect_device",
                        "arguments": {"serial": "phone"},
                    },
                },
            )
            wait_until(lambda: "connect-start" in events(path), process, log)
            process.send_signal(signal.SIGINT)
            wait_until(lambda: "disconnect-start" in events(path), process, log)
            assert "connect-rollback" in events(path)
            process.send_signal(signal.SIGINT)
            path.with_suffix(".cleanup-release").touch()
            assert process.wait(timeout=10) == 0, log.read_text()
            try:
                request.result(timeout=5)
            except httpx.TransportError:
                pass
        assert events(path).index("connect-rollback") < events(path).index(
            "deinit-start"
        )


def test_bind_failure_cleans_up_initialized_library(tmp_path):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with child(tmp_path, port=occupied.getsockname()[1]) as (process, path, log, _):
            assert process.wait(timeout=10) != 0, log.read_text()
            assert events(path) == [
                "init-start",
                "init-end",
                "deinit-start",
                "deinit-end",
            ]
