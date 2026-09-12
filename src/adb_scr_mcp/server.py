"""Uvicorn runner that keeps the loop alive until application cleanup finishes."""

import argparse
import asyncio
import logging
import math
import signal
import threading
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI

from adb_scr.async_utils import complete_on_cancel

from .app import create_app

__all__ = []
logger = logging.getLogger(__name__)


class GracefulServer(uvicorn.Server):
    def handle_exit(self, sig, frame) -> None:
        # Never set force_exit: a second SIGINT must not skip lifespan shutdown.
        self.should_exit = True
        self.config.app.state.runtime.request_shutdown()

    @contextmanager
    def capture_signals(self):
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        previous = {
            sig: signal.signal(sig, self.handle_exit)
            for sig in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        # Do not re-raise captured signals into asyncio.run() after cleanup.

    async def serve(self, sockets=None) -> None:
        with self.capture_signals():
            serving = asyncio.create_task(self._serve(sockets), name="adb-scr-mcp-http")
            try:
                await asyncio.shield(serving)
            except asyncio.CancelledError:
                self.handle_exit(signal.SIGTERM, None)
                await complete_on_cancel(serving)
                raise
            finally:
                # Also cover startup interruption/partial socket startup. Uvicorn
                # normally sends shutdown itself, so never send it a second time.
                lifespan = getattr(self, "lifespan", None)
                if (
                    lifespan is not None
                    and lifespan.startup_event.is_set()
                    and not lifespan.shutdown_event.is_set()
                ):
                    await complete_on_cancel(lifespan.shutdown())
        if self.lifespan.startup_failed or self.lifespan.shutdown_failed:
            raise RuntimeError("MCP application lifespan failed; see server logs")


async def serve(
    app: FastAPI | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    adb_path: str | None = None,
    drain_timeout: float = 10.0,
) -> None:
    """Run one local server; cancellation/SIGINT/SIGTERM waits for cleanup.

    drain_timeout bounds HTTP request draining, not native resource destruction.
    Run on the main thread to install signal handlers. An embedded caller on
    another thread owns signal handling and can cancel this coroutine to stop.
    """
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("This unauthenticated server only supports loopback hosts")
    if not math.isfinite(drain_timeout) or drain_timeout < 0:
        raise ValueError("drain_timeout must be a finite nonnegative number")
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    app = app if app is not None else create_app(adb_path)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        loop="asyncio",
        lifespan="on",
        workers=1,
        timeout_graceful_shutdown=drain_timeout,
    )
    await GracefulServer(config).serve()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local Android MCP server with graceful shutdown"
    )
    parser.add_argument(
        "--host", choices=["127.0.0.1", "localhost", "::1"], default="127.0.0.1"
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--adb-path", default=None, help="ADB executable path (default: adb from PATH)"
    )
    parser.add_argument(
        "--drain-timeout",
        type=float,
        default=10.0,
        help="Seconds to drain HTTP requests before cancellation; cleanup is always awaited",
    )
    args = parser.parse_args()
    try:
        asyncio.run(
            serve(
                host=args.host,
                port=args.port,
                adb_path=args.adb_path,
                drain_timeout=args.drain_timeout,
            )
        )
    except (RuntimeError, ValueError) as error:
        logger.error("%s", error)
        raise SystemExit(1) from None
