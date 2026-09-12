"""Own the library, device sessions and in-flight MCP operations on one loop."""

import asyncio
import logging
from contextlib import asynccontextmanager

import adb_scr
from adb_scr.async_utils import complete_on_cancel

__all__ = []
logger = logging.getLogger(__name__)
_owner: object | None = None


class Runtime:
    def __init__(self, adb_path: str | None = None) -> None:
        self.adb_path = adb_path
        self.ready = False
        self.closing = False
        self.versions: tuple[str, str] | None = None
        self.devices: dict[str, adb_scr.AndroidDevice] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._operations: set[asyncio.Task] = set()
        self._init_attempted = False
        self._close_task: asyncio.Task | None = None

    async def start(self) -> None:
        global _owner
        if _owner is not None or adb_scr.DAEMON_RUNNING:
            raise RuntimeError(
                "MCP requires ownership of adb_scr; it is already initialized"
            )
        # No await between checking and claiming ownership on this event loop.
        # init_lib's own lock cannot distinguish a new owner from a borrower.
        _owner = self
        await complete_on_cancel(self._initialize())

    async def _initialize(self) -> None:
        self._init_attempted = True
        self.versions = await adb_scr.init_lib(self.adb_path)
        self.ready = not self.closing

    def request_shutdown(self) -> None:
        self.closing = True
        self.ready = False

    @asynccontextmanager
    async def operation(self):
        if not self.ready or self.closing:
            raise RuntimeError("MCP server is not ready or is shutting down")
        task = asyncio.current_task()
        self._operations.add(task)
        try:
            yield
        finally:
            self._operations.discard(task)

    def info(self, serial: str) -> dict:
        device = self.devices.get(serial)
        size = device.get_screen_size() if device else None
        return {
            "serial": serial,
            "connected": bool(device and device.is_connected),
            "connection_type": device.connection_type if device else None,
            "screen_size": {"width": size[0], "height": size[1]} if size else None,
            "last_disconnect_reason": device.last_disconnect_reason if device else None,
        }

    async def list_devices(self) -> dict:
        async with self.operation():
            available = await adb_scr.list_devices()
            serials = dict.fromkeys([*available, *self.devices])
            return {"devices": [self.info(serial) for serial in serials]}

    async def connect(self, serial: str, connection_type: str) -> dict:
        async with self.operation():
            if serial not in self.devices:
                # Register ownership before the first await, including failed connects.
                self.devices[serial] = adb_scr.AndroidDevice(serial, connection_type)
                self._locks[serial] = asyncio.Lock()
            async with self._locks[serial]:
                device = self.devices[serial]
                if device.connection_type != connection_type:
                    raise ValueError(
                        "Device already registered with a different connection_type"
                    )
                if not device.is_connected and not await device.connect():
                    raise RuntimeError(
                        f"Unable to connect {serial}: {device.last_disconnect_reason}"
                    )
                return self.info(serial)

    @asynccontextmanager
    async def device(self, serial: str, *, require_connected: bool = True):
        async with self.operation():
            if serial not in self.devices:
                raise ValueError(f"Unknown device {serial}; call connect_device first")
            async with self._locks[serial]:
                device = self.devices[serial]
                if require_connected and not device.is_connected:
                    raise RuntimeError(
                        f"Device {serial} is disconnected; reconnect explicitly"
                    )
                yield device

    async def close(self) -> None:
        self.request_shutdown()
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._close(), name="adb-scr-mcp-cleanup"
            )
        await complete_on_cancel(self._close_task)

    async def _close(self) -> None:
        global _owner
        operations = list(self._operations)
        for task in operations:
            task.cancel()
        # connect()/native work may finish cancellation rollback asynchronously.
        await asyncio.gather(*operations, return_exceptions=True)
        results = await asyncio.gather(
            *(device.disconnect() for device in self.devices.values()),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if self._init_attempted:
            try:
                await adb_scr.deinit_lib()
            except Exception as error:  # noqa: BLE001 - report all cleanup failures below
                errors.append(error)
        if _owner is self:
            _owner = None
        for error in errors:
            logger.error(
                "MCP cleanup failed", exc_info=(type(error), error, error.__traceback__)
            )
        if errors:
            raise RuntimeError(
                f"MCP cleanup failed ({len(errors)} errors)"
            ) from errors[0]
        logger.info("All MCP device sessions and adb_scr resources closed")
