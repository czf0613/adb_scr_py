import asyncio
import base64
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("mcp")
import httpx

import adb_scr
from adb_scr_mcp import create_app


def test_recording_quality_schema_and_forwarding(fake_adb, tmp_path):
    async def run():
        _, instances, _ = fake_adb
        async with client_for(create_app()) as client:
            catalog = await rpc(client, "tools/list")
            tool = next(tool for tool in catalog["tools"] if tool["name"] == "start_recording")
            field = tool["inputSchema"]["properties"]["quality"]
            assert field["default"] == 0.75
            assert field["minimum"] == 0.0 and field["maximum"] == 1.0
            await call(client, "connect_device", serial="phone")
            for quality in [-0.1, 1.1, True, "0.75"]:
                result = await call(client, "start_recording", serial="phone",
                                    output_file=str(tmp_path / "invalid.mp4"), quality=quality)
                assert result["isError"]
            assert instances[0].calls == []
            for options, expected in [({}, 0.75), ({"quality": 0.45}, 0.45),
                                      ({"quality": 0}, 0.0), ({"quality": 1}, 1.0)]:
                output = str(tmp_path / f"quality-{expected}.mp4")
                result = await call(client, "start_recording", serial="phone",
                                    output_file=output, **options)
                assert not result["isError"]
                assert instances[0].calls[-1] == ("start_recording", output, expected)

    asyncio.run(run())


@pytest.fixture
def fake_adb(monkeypatch):
    events = []
    instances = []

    async def init(adb_path=None):
        events.append(("init", adb_path))
        return "test-adb", "3.2"

    async def deinit():
        events.append("deinit")

    async def devices():
        return ["phone"]

    class Device:
        def __init__(self, serial, connection_type):
            self.serial = serial
            self.connection_type = connection_type
            self.is_connected = False
            self.last_disconnect_reason = None
            self.calls = []
            instances.append(self)

        async def connect(self):
            events.append("connect")
            self.is_connected = True
            return True

        async def disconnect(self):
            events.append("disconnect")
            self.is_connected = False

        def get_screen_size(self):
            return (100, 200) if self.is_connected else None

        async def get_screenshot_jpg(self, quality, scale, roi):
            self.calls.append(("screenshot", quality, scale, roi))
            return b"\xff\xd8synthetic-jpeg\xff\xd9"

        async def click(self, x, y):
            self.calls.append(("click", x, y))

        async def action_series(self, actions):
            self.calls.append(("actions", actions))

        async def launch_app(self, package_name, activity_name):
            self.calls.append(("launch", package_name, activity_name))
            return True

        async def stop_app(self, package_name):
            self.calls.append(("stop", package_name))
            return True

        async def start_recording(self, output_file, quality=0.75):
            self.calls.append(("start_recording", output_file, quality))

        async def stop_recording(self):
            self.calls.append(("stop_recording",))

    monkeypatch.setattr(adb_scr, "init_lib", init)
    monkeypatch.setattr(adb_scr, "deinit_lib", deinit)
    monkeypatch.setattr(adb_scr, "list_devices", devices)
    monkeypatch.setattr(adb_scr, "AndroidDevice", Device)
    return events, instances, Device


@asynccontextmanager
async def client_for(app):
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        yield client


async def rpc(client, method, params=None):
    response = await client.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def call(client, name, **arguments):
    return await rpc(client, "tools/call", {"name": name, "arguments": arguments})


def test_mcp_http_lifespan_tools_and_image(fake_adb):
    events, instances, _ = fake_adb

    async def run():
        app = create_app(adb_path="/custom/adb")
        assert events == []  # Import/construction must never start ADB.
        async with client_for(app) as client:
            result = await rpc(
                client,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            )
            assert result["capabilities"]["tools"] is not None
            catalog = await rpc(client, "tools/list")
            assert {
                "list_devices",
                "connect_device",
                "screenshot",
                "click",
                "action_series",
                "start_recording",
                "stop_recording",
            } <= {tool["name"] for tool in catalog["tools"]}
            assert (await client.get("/healthz")).status_code == 200
            listed = await call(client, "list_devices")
            assert listed["structuredContent"]["devices"][0]["serial"] == "phone"
            connected = await call(client, "connect_device", serial="phone")
            assert connected["structuredContent"]["connected"] is True
            await call(client, "connect_device", serial="phone")
            assert len(instances) == 1
            assert events.count("connect") == 1
            shot = await call(
                client,
                "screenshot",
                serial="phone",
                quality=80,
                scale=0.5,
                roi=[1, 2, 20, 30],
            )
            assert not shot.get("isError")
            content = shot["content"][0]
            assert content["type"] == "image"
            assert content["mimeType"] == "image/jpeg"
            assert (
                base64.b64decode(content["data"]) == b"\xff\xd8synthetic-jpeg\xff\xd9"
            )
            assert instances[0].calls == [("screenshot", 80, 0.5, (1, 2, 20, 30))]
        assert events == [("init", "/custom/adb"), "connect", "disconnect", "deinit"]

    asyncio.run(run())


def test_agent_guide_is_discoverable_and_readable_without_device_connection(fake_adb):
    _, instances, _ = fake_adb

    async def run():
        async with client_for(create_app()) as client:
            initialized = await rpc(
                client,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "agent", "version": "1"},
                },
            )
            assert "get_agent_guide" in initialized["instructions"]
            catalog = await rpc(client, "resources/list")
            guide = next(
                resource
                for resource in catalog["resources"]
                if resource["uri"] == "adb-scr://guide"
            )
            assert guide["mimeType"] == "text/markdown"
            resource = await rpc(client, "resources/read", {"uri": guide["uri"]})
            text = resource["contents"][0]["text"]
            result = await call(client, "get_agent_guide")
            assert not result["isError"]
            assert result["content"][0]["text"] == text
            assert len(text) > 500  # Catch absent/empty packaged guide content.
            assert instances == []

    asyncio.run(run())


def test_tool_metadata_distinguishes_observation_from_device_control(fake_adb):
    async def run():
        async with client_for(create_app()) as client:
            catalog = await rpc(client, "tools/list")
            tools = {tool["name"]: tool for tool in catalog["tools"]}
            for name in (
                "get_agent_guide",
                "list_devices",
                "get_device_info",
                "screenshot",
            ):
                assert tools[name].get("annotations", {}).get("readOnlyHint") is True
            for name in (
                "connect_device",
                "click",
                "paste",
                "start_recording",
                "stop_app",
            ):
                assert (
                    tools[name].get("annotations", {}).get("readOnlyHint") is not True
                )

    asyncio.run(run())


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("click", {"serial": "phone", "x": 100, "y": 0}),
        ("click", {"serial": "phone", "x": True, "y": 0}),
        ("screenshot", {"serial": "phone", "quality": 101}),
        ("screenshot", {"serial": "phone", "scale": 0}),
        ("screenshot", {"serial": "phone", "scale": True}),
        (
            "launch_app",
            {
                "serial": "phone",
                "package_name": "com.example;reboot",
                "activity_name": ".Main",
            },
        ),
        ("stop_app", {"serial": "phone", "package_name": "$(reboot)"}),
        (
            "action_series",
            {"serial": "phone", "actions": [{"x": 1, "y": 1, "action": "DOWN"}]},
        ),
        (
            "action_series",
            {
                "serial": "phone",
                "actions": [
                    {"x": 1, "y": 1, "action": "MOVE"},
                    {"x": 1, "y": 1, "action": "UP"},
                ],
            },
        ),
    ],
)
def test_invalid_tool_input_reports_error_without_device_command(
    fake_adb, name, arguments
):
    _, instances, _ = fake_adb

    async def run():
        async with client_for(create_app()) as client:
            await call(client, "connect_device", serial="phone")
            result = await call(client, name, **arguments)
            assert result["isError"] is True
            assert instances[0].calls == []

    asyncio.run(run())


def test_disconnected_device_and_missing_frame_are_tool_errors(fake_adb, monkeypatch):
    _, _, Device = fake_adb

    async def no_frame(self, *args, **kwargs):
        return None

    monkeypatch.setattr(Device, "get_screenshot_jpg", no_frame)

    async def run():
        async with client_for(create_app()) as client:
            result = await call(client, "screenshot", serial="unknown")
            assert result["isError"] is True
            await call(client, "connect_device", serial="phone")
            result = await call(client, "screenshot", serial="phone")
            assert result["isError"] is True

    asyncio.run(run())


def test_shutdown_rejects_new_work_and_waits_despite_cancellation(
    fake_adb, monkeypatch
):
    events, _, Device = fake_adb

    async def run():
        closing = asyncio.Event()
        release = asyncio.Event()

        async def disconnect(self):
            events.append("disconnect-start")
            closing.set()
            await release.wait()
            events.append("disconnect-end")

        monkeypatch.setattr(Device, "disconnect", disconnect)
        app = create_app()
        async with client_for(app) as client:
            await call(client, "connect_device", serial="phone")
            close_task = asyncio.create_task(app.state.runtime.close())
            await asyncio.wait_for(closing.wait(), 2)
            close_task.cancel()
            await asyncio.sleep(0)
            assert not close_task.done()
            assert "deinit" not in events
            assert (await client.get("/healthz")).status_code == 503
            assert (await call(client, "list_devices"))["isError"] is True
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await close_task
        assert events[-2:] == ["disconnect-end", "deinit"]
        assert events.count("deinit") == 1

    asyncio.run(run())


def test_shutdown_cancels_inflight_connect_before_deinit(fake_adb, monkeypatch):
    events, _, Device = fake_adb

    async def run():
        connecting = asyncio.Event()

        async def connect(self):
            connecting.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                events.append("connect-rollback")

        monkeypatch.setattr(Device, "connect", connect)
        app = create_app()
        async with app.router.lifespan_context(app):
            task = asyncio.create_task(app.state.runtime.connect("phone", "usb"))
            await asyncio.wait_for(connecting.wait(), 2)
            await app.state.runtime.close()
            assert task.cancelled()
        assert events[-3:] == ["connect-rollback", "disconnect", "deinit"]

    asyncio.run(run())


def test_cleanup_continues_after_one_disconnect_fails(fake_adb, monkeypatch):
    events, _, Device = fake_adb

    async def disconnect(self):
        events.append("disconnect:" + self.serial)
        if self.serial == "bad":
            raise RuntimeError("disconnect failure")

    monkeypatch.setattr(Device, "disconnect", disconnect)

    async def run():
        app = create_app()
        with pytest.raises(RuntimeError, match="cleanup"):
            async with app.router.lifespan_context(app):
                await app.state.runtime.connect("bad", "usb")
                await app.state.runtime.connect("good", "usb")
        assert "disconnect:bad" in events
        assert "disconnect:good" in events
        assert events[-1] == "deinit"

    asyncio.run(run())


def test_startup_failure_runs_cleanup(fake_adb, monkeypatch):
    events, _, _ = fake_adb

    async def fail(adb_path=None):
        events.append("init-failed")
        raise RuntimeError("startup failure")

    monkeypatch.setattr(adb_scr, "init_lib", fail)

    async def run():
        app = create_app()
        with pytest.raises(RuntimeError, match="startup failure"):
            async with app.router.lifespan_context(app):
                pytest.fail("Startup must not succeed")
        assert events == ["init-failed", "deinit"]

    asyncio.run(run())


def test_control_and_recording_tools_preserve_arguments(
    fake_adb, monkeypatch, tmp_path
):
    _, instances, Device = fake_adb

    async def launch(self, package_name, activity_name):
        self.calls.append(("launch", package_name, activity_name))
        return True

    monkeypatch.setattr(Device, "launch_app", launch, raising=False)

    async def run():
        async with client_for(create_app()) as client:
            await call(client, "connect_device", serial="phone")
            assert not (await call(client, "click", serial="phone", x=99, y=199))[
                "isError"
            ]
            assert not (
                await call(
                    client,
                    "launch_app",
                    serial="phone",
                    package_name="com.example.app",
                    activity_name=".Main",
                )
            )["isError"]
            result = await call(
                client,
                "action_series",
                serial="phone",
                actions=[
                    {
                        "x": 1,
                        "y": 2,
                        "action": "DOWN",
                        "pointer_id": 4,
                        "duration_ms": 0,
                    },
                    {"x": 3, "y": 4, "action": "UP", "pointer_id": 4, "duration_ms": 5},
                ],
            )
            assert not result["isError"]
            output = str(tmp_path / "capture.mp4")
            result = await call(
                client, "start_recording", serial="phone", output_file=output
            )
            assert result["structuredContent"]["output_file"] == output
            await call(client, "disconnect_device", serial="phone")
            assert not (await call(client, "stop_recording", serial="phone"))["isError"]
            assert instances[0].calls == [
                ("click", 99, 199),
                ("launch", "com.example.app", ".Main"),
                (
                    "actions",
                    [
                        adb_scr.GestureActionNode(
                            1, 2, adb_scr.GestureAction.DOWN, 0, 4
                        ),
                        adb_scr.GestureActionNode(3, 4, adb_scr.GestureAction.UP, 5, 4),
                    ],
                ),
                ("start_recording", output, 0.75),
                ("stop_recording",),
            ]

    asyncio.run(run())


def test_runner_cancellation_waits_for_cleanup_and_restores_handlers(
    fake_adb, monkeypatch
):
    import signal

    from adb_scr_mcp.server import serve

    events, _, _ = fake_adb

    async def run():
        cleaning = asyncio.Event()
        release = asyncio.Event()

        async def deinit():
            cleaning.set()
            await release.wait()
            events.append("deinit")

        monkeypatch.setattr(adb_scr, "deinit_lib", deinit)
        before = [signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)]
        app = create_app()
        runner = asyncio.create_task(serve(app, port=0, drain_timeout=0))
        for _ in range(100):
            if app.state.runtime.ready:
                break
            await asyncio.sleep(0.01)
        assert app.state.runtime.ready
        runner.cancel()
        await asyncio.wait_for(cleaning.wait(), 2)
        runner.cancel()
        await asyncio.sleep(0)
        assert not runner.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await runner
        assert events[-1] == "deinit"
        assert [
            signal.getsignal(signal.SIGINT),
            signal.getsignal(signal.SIGTERM),
        ] == before

    asyncio.run(run())


def test_rejects_borrowing_an_initialized_library(fake_adb, monkeypatch):
    events, _, _ = fake_adb
    monkeypatch.setattr(adb_scr, "DAEMON_RUNNING", True)

    async def run():
        app = create_app()
        with pytest.raises(RuntimeError, match="already initialized"):
            async with app.router.lifespan_context(app):
                pytest.fail("Must not take ownership of an existing library")
        assert events == []

    asyncio.run(run())


def test_mcp_rejects_untrusted_host_and_origin(fake_adb):
    async def run():
        async with client_for(create_app()) as client:
            for headers in (
                {"Host": "evil.example"},
                {"Origin": "https://evil.example"},
            ):
                response = await client.post("/mcp", headers=headers, json={})
                assert response.status_code in (403, 421)

    asyncio.run(run())


def test_concurrent_lifespans_cannot_share_library_ownership(fake_adb, monkeypatch):
    events, _, _ = fake_adb

    async def run():
        initializing = asyncio.Event()
        release = asyncio.Event()

        async def init(adb_path=None):
            events.append("init")
            initializing.set()
            await release.wait()
            return "fake-adb", "3.2"

        monkeypatch.setattr(adb_scr, "init_lib", init)
        first = create_app()
        second = create_app()
        starting = asyncio.create_task(first.state.runtime.start())
        try:
            await asyncio.wait_for(initializing.wait(), 2)

            async def enter_second():
                async with second.router.lifespan_context(second):
                    pass

            competing = asyncio.create_task(enter_second())
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(RuntimeError, match="ownership"):
                await competing
            assert events == ["init"]
        finally:
            release.set()
            await starting
            await first.state.runtime.close()
        assert events == ["init", "deinit"]
        # A finished owner must not block a fresh server in the same loop.
        fresh = create_app()
        async with fresh.router.lifespan_context(fresh):
            assert events[-1] == "init"

    asyncio.run(run())


def test_anyio_startup_cancellation_waits_for_init_and_cleanup(fake_adb, monkeypatch):
    import anyio

    events, _, _ = fake_adb

    async def run():
        initializing = asyncio.Event()
        release_init = asyncio.Event()
        cleaning = asyncio.Event()
        release_cleanup = asyncio.Event()
        scope_ready = asyncio.Future()

        async def init(adb_path=None):
            initializing.set()
            await release_init.wait()
            events.append("init-end")
            return "fake-adb", "3.2"

        async def deinit():
            cleaning.set()
            await release_cleanup.wait()
            events.append("deinit-end")

        monkeypatch.setattr(adb_scr, "init_lib", init)
        monkeypatch.setattr(adb_scr, "deinit_lib", deinit)
        app = create_app()

        async def start():
            with anyio.CancelScope() as scope:
                scope_ready.set_result(scope)
                async with app.router.lifespan_context(app):
                    pytest.fail("Cancelled startup must not become ready")

        starting = asyncio.create_task(start())
        scope = await scope_ready
        await initializing.wait()
        scope.cancel()
        await asyncio.sleep(0)
        assert not starting.done()
        assert not cleaning.is_set()
        release_init.set()
        await asyncio.wait_for(cleaning.wait(), 2)
        assert not starting.done()
        assert events == ["init-end"]
        release_cleanup.set()
        # The library's complete_on_cancel re-raises CancelledError after work
        # finishes; it does not retain AnyIO's private scope cancellation marker.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(starting, 2)
        assert events == ["init-end", "deinit-end"]

    asyncio.run(run())
