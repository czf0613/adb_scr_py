"""FastAPI composition; importing this module never starts ADB."""

from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .guidance import SERVER_INSTRUCTIONS, register_guidance
from .runtime import Runtime
from .tools import register_tools

__all__ = []


def create_app(adb_path: str | None = None) -> FastAPI:
    """Create a single-lifespan app owning adb_scr and its device sessions.

    Mounts Streamable HTTP at /mcp and readiness at /healthz. No phone is
    connected automatically. Use ``python -m adb_scr_mcp`` for the signal-safe
    runner; the app factory alone cannot control its ASGI host's signal policy.
    """
    runtime = Runtime(adb_path)
    mcp = FastMCP(
        "adb-scr",
        instructions=SERVER_INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[
                "127.0.0.1",
                "127.0.0.1:*",
                "localhost",
                "localhost:*",
                "[::1]",
                "[::1]:*",
            ],
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
            ],
        ),
    )
    register_guidance(mcp)
    register_tools(mcp, runtime)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await runtime.start()
            async with mcp.session_manager.run():
                yield
        finally:
            # AnyIO uses level cancellation; shield cleanup from an enclosing scope
            # as well as ordinary asyncio task cancellation (handled by Runtime).
            with anyio.CancelScope(shield=True):
                await runtime.close()

    app = FastAPI(title="Android MCP Server", lifespan=lifespan)
    app.state.runtime = runtime

    @app.get("/healthz")
    async def health():
        return JSONResponse(
            {
                "status": "ready" if runtime.ready else "stopping",
                "versions": runtime.versions,
            },
            status_code=200 if runtime.ready else 503,
        )

    # Mount at root so the SDK's /mcp route is not doubled or redirected.
    app.mount("/", mcp_app)
    return app
