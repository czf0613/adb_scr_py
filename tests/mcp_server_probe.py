"""Real HTTP/signal test process with all phone/ADB effects replaced."""

import asyncio
import sys
from pathlib import Path

import adb_scr
from adb_scr.async_utils import complete_on_cancel
from adb_scr_mcp import create_app
from adb_scr_mcp.server import serve


async def main():
    event_file = Path(sys.argv[1])
    port = int(sys.argv[2])
    mode = sys.argv[3]

    def record(event):
        with event_file.open("a") as output:
            output.write(event + "\n")

    async def gate(name):
        path = event_file.with_suffix("." + name)
        for _ in range(1000):
            if path.exists():
                return
            await asyncio.sleep(0.01)
        raise TimeoutError(name)

    async def init(adb_path=None):
        record("init-start")
        if mode == "startup":
            await gate("init-release")
        record("init-end")
        return "fake-adb", "3.2"

    async def deinit():
        record("deinit-start")
        if mode == "startup":
            await gate("cleanup-release")
        await asyncio.sleep(0.01)
        record("deinit-end")

    class Device:
        def __init__(self, serial, connection_type):
            self.serial = serial
            self.connection_type = connection_type
            self.is_connected = False
            self.last_disconnect_reason = None

        def get_screen_size(self):
            return (100, 200) if self.is_connected else None

        async def connect(self):
            record("connect-start")
            if mode == "connecting":

                async def rollback():
                    await asyncio.sleep(0.01)
                    record("connect-rollback")

                try:
                    await asyncio.Event().wait()
                finally:
                    # Mirror AndroidDevice.connect's cancellation-safe rollback.
                    await complete_on_cancel(rollback())
            self.is_connected = True
            record("connect-end")
            return True

        async def disconnect(self):
            record("disconnect-start")
            await gate("cleanup-release")
            self.is_connected = False
            record("disconnect-end")

    adb_scr.init_lib = init
    adb_scr.deinit_lib = deinit
    adb_scr.AndroidDevice = Device
    await serve(create_app(), port=port, drain_timeout=0.15)
    record("loop-still-running")
    await asyncio.sleep(0)
    record("serve-returned")


if __name__ == "__main__":
    asyncio.run(main())
