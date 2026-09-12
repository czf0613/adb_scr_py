"""MCP input validation and adapters for adb_scr's public API."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

import adb_scr

from .runtime import Runtime

__all__ = []

Serial = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^\s\x00]+$")]
Coordinate = Annotated[int, Field(strict=True, ge=0, le=65535)]
Duration = Annotated[int, Field(strict=True, ge=0, le=10000)]
Package = Annotated[
    str, Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_.]*$")
]
Activity = Annotated[
    str, Field(min_length=1, max_length=255, pattern=r"^\.?[A-Za-z_][A-Za-z0-9_.]*$")
]


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: Coordinate
    y: Coordinate
    action: Literal["DOWN", "MOVE", "UP"]
    duration_ms: Duration = 50
    pointer_id: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)] = 0


def check_point(device, x: int, y: int) -> None:
    size = device.get_screen_size()
    if size is None or not (0 <= x < size[0] and 0 <= y < size[1]):
        raise ValueError("Coordinates are outside the original screen dimensions")


def submitted(device) -> dict[str, Any]:
    if not device.is_connected:
        raise RuntimeError("Device disconnected during the operation")
    return {"status": "submitted", "serial": device.serial}


def register_tools(mcp: FastMCP, runtime: Runtime) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_devices() -> dict[str, Any]:
        """List ADB serials and managed sessions. Listed devices may be offline/unauthorized."""
        return await runtime.list_devices()

    @mcp.tool()
    async def connect_device(
        serial: Serial, connection_type: Literal["usb", "tcp"] = "usb"
    ) -> dict[str, Any]:
        """Connect a USB serial or TCP IP:port; wait for metadata, not necessarily the first frame."""
        return await runtime.connect(serial, connection_type)

    @mcp.tool()
    async def disconnect_device(serial: Serial) -> dict[str, Any]:
        """Disconnect a managed device and finalize any recording. Repeated calls are safe."""
        async with runtime.device(serial, require_connected=False) as device:
            await device.disconnect()
            return runtime.info(serial)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_device_info(serial: Serial) -> dict[str, Any]:
        """Get connection state, original screen dimensions and last disconnect reason."""
        async with runtime.device(serial, require_connected=False):
            return runtime.info(serial)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def screenshot(
        serial: Serial,
        quality: Annotated[int, Field(strict=True, ge=1, le=100)] = 75,
        scale: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)] = 1.0,
        roi: tuple[
            Coordinate,
            Coordinate,
            Annotated[int, Field(strict=True, gt=0)],
            Annotated[int, Field(strict=True, gt=0)],
        ]
        | None = None,
    ) -> Image:
        """Return JPEG image content. ROI is original (x,y,width,height); crop precedes scale.

        No frame yet is a retryable tool error. Touch coordinates always use the
        original screen; convert scaled/cropped image coordinates before acting.
        """
        async with runtime.device(serial) as device:
            data = await device.get_screenshot_jpg(quality, scale, roi)
            if data is None:
                raise RuntimeError(
                    "No screenshot available; wait for a decoded frame and retry"
                )
            return Image(data=data, format="jpeg")

    @mcp.tool()
    async def click(serial: Serial, x: Coordinate, y: Coordinate) -> dict[str, Any]:
        """Tap original screen coordinates; returns submission status, not UI confirmation."""
        async with runtime.device(serial) as device:
            check_point(device, x, y)
            await device.click(x, y)
            return submitted(device)

    @mcp.tool()
    async def double_click(
        serial: Serial, x: Coordinate, y: Coordinate
    ) -> dict[str, Any]:
        """Double tap original screen coordinates."""
        async with runtime.device(serial) as device:
            check_point(device, x, y)
            await device.double_click(x, y)
            return submitted(device)

    @mcp.tool()
    async def long_press(
        serial: Serial, x: Coordinate, y: Coordinate, duration_ms: Duration
    ) -> dict[str, Any]:
        """Hold then release at original screen coordinates, for up to 10000 ms."""
        async with runtime.device(serial) as device:
            check_point(device, x, y)
            await device.long_press(x, y, duration_ms)
            return submitted(device)

    @mcp.tool()
    async def swipe(
        serial: Serial, x1: Coordinate, y1: Coordinate, x2: Coordinate, y2: Coordinate
    ) -> dict[str, Any]:
        """Swipe from (x1,y1) to (x2,y2), using original screen pixels."""
        async with runtime.device(serial) as device:
            check_point(device, x1, y1)
            check_point(device, x2, y2)
            await device.swipe(x1, y1, x2, y2)
            return submitted(device)

    @mcp.tool()
    async def action_series(
        serial: Serial,
        actions: Annotated[list[Action], Field(min_length=2, max_length=1000)],
    ) -> dict[str, Any]:
        """Send ordered single/multi-finger gestures; each pointer must finish with UP.

        Up to 10 simultaneous fingers. duration_ms is the wait after each node,
        including the final node, with the library's timing jitter.
        """
        async with runtime.device(serial) as device:
            active = set()
            nodes = []
            for action in actions:
                check_point(device, action.x, action.y)
                pointer = action.pointer_id
                if action.action == "DOWN":
                    if pointer in active or len(active) >= 10:
                        raise ValueError(
                            "Duplicate DOWN or more than 10 active pointers"
                        )
                    active.add(pointer)
                else:
                    if pointer not in active:
                        raise ValueError("MOVE/UP requires a matching DOWN")
                    if action.action == "UP":
                        active.remove(pointer)
                nodes.append(
                    adb_scr.GestureActionNode(
                        action.x,
                        action.y,
                        adb_scr.GestureAction[action.action],
                        action.duration_ms,
                        action.pointer_id,
                    )
                )
            if active:
                raise ValueError("Every pointer must finish with UP")
            await device.action_series(nodes)
            return submitted(device)

    @mcp.tool()
    async def press_back(serial: Serial) -> dict[str, Any]:
        """Press Android's Back button."""
        async with runtime.device(serial) as device:
            await device.press_back()
            return submitted(device)

    @mcp.tool()
    async def paste(
        serial: Serial, text: Annotated[str, Field(max_length=100000)]
    ) -> dict[str, Any]:
        """Paste text into the focused Android input field; focus the field first."""
        async with runtime.device(serial) as device:
            await device.paste(text)
            return submitted(device)

    @mcp.tool()
    async def launch_app(
        serial: Serial, package_name: Package, activity_name: Activity
    ) -> dict[str, Any]:
        """Launch an Android package/activity; reports the ADB command result."""
        async with runtime.device(serial) as device:
            if not await device.launch_app(package_name, activity_name):
                raise RuntimeError("ADB failed to launch the application")
            return submitted(device)

    @mcp.tool()
    async def stop_app(serial: Serial, package_name: Package) -> dict[str, Any]:
        """Force-stop an Android package; reports the ADB command result."""
        async with runtime.device(serial) as device:
            if not await device.stop_app(package_name):
                raise RuntimeError("ADB failed to stop the application")
            return submitted(device)

    @mcp.tool()
    async def start_recording(
        serial: Serial, output_file: Annotated[str, Field(min_length=1)],
        quality: Annotated[float, Field(
            strict=True, ge=0.0, le=1.0, allow_inf_nan=False,
            description=(
                "H.264 recording quality, 0.0 to 1.0; default 0.75. "
                "Higher values usually improve quality and increase file size. "
                "This is not the screenshot quality scale of 1 to 100, a bitrate, "
                "or a file-size ratio; 1.0 does not guarantee lossless H.264."
            ),
        )] = 0.75,
    ) -> dict[str, Any]:
        """Record the screen and available audio to a new MP4 on the server computer.

        Keep quality=0.75 for the default balance, lower it for smaller files, or
        raise it for higher quality. Actual size depends on content and hardware;
        no fixed size reduction is guaranteed. Audio is passed through unchanged.
        The parent directory must exist, and existing files are never overwritten.
        """
        async with runtime.device(serial) as device:
            await device.start_recording(output_file, quality=quality)
            return {"status": "recording", "serial": serial, "output_file": output_file}

    @mcp.tool()
    async def stop_recording(serial: Serial) -> dict[str, Any]:
        """Finalize the latest MP4 and report write errors, including after disconnection."""
        async with runtime.device(serial, require_connected=False) as device:
            await device.stop_recording()
            return {"status": "stopped", "serial": serial}
