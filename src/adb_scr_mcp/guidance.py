"""Agent discovery surfaces backed by one packaged operating guide."""

from importlib.resources import files

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

__all__ = []

SERVER_INSTRUCTIONS = (
    "Control a user-authorized Android phone through ADB/scrcpy. "
    "Before the first device action, call get_agent_guide or read adb-scr://guide. "
    "Tools cover device discovery/connections, JPEG screenshots, touch gestures, "
    "text paste, Back, app launch/stop and MP4 recording. "
    "List devices, choose the requested serial, connect explicitly, then screenshot. "
    "Use an observe-act-observe loop: inspect each screenshot before choosing actions. "
    "Coordinates always use original screen pixels with top-left origin, even after "
    "cropping/scaling a screenshot. Submitted gestures are not UI confirmation. "
    "There is no UI hierarchy, OCR, shell or device-identifier query tool; read screen "
    "content visually. Do not bypass a locked screen; ask the user to unlock it."
)


def register_guidance(mcp: FastMCP) -> None:
    guide = files("adb_scr_mcp").joinpath("agent_guide.md").read_text(encoding="utf-8")

    @mcp.resource(
        "adb-scr://guide",
        name="agent_guide",
        description="Read before controlling a phone: capabilities, workflow, coordinates and errors.",
        mime_type="text/markdown",
    )
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    def get_agent_guide() -> str:
        """Read this first: the operating guide for all Android MCP tools. No phone connection needed."""
        return guide
