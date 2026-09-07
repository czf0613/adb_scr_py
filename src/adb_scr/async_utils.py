"""Internal cancellation-safe resource operations."""

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

__all__ = []
_T = TypeVar("_T")


async def complete_on_cancel(operation: Awaitable[_T]) -> _T:
    """Wait for owned work to finish before propagating caller cancellation."""
    task = asyncio.ensure_future(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        # Retrieve any exception, while preserving the caller's cancellation.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def close_writer(writer: asyncio.StreamWriter, timeout: float) -> None:
    """Close a stream, aborting the transport if graceful closing stalls."""
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout)
    except (Exception, asyncio.CancelledError):
        writer.transport.abort()
        raise


async def stop_process(
    process: asyncio.subprocess.Process, timeout: float = 5.0
) -> None:
    """Kill an owned subprocess if necessary and reap its exit status."""
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    # A killed process can still have paused PIPE readers. Drain them so the
    # subprocess transport can finish closing before we report completion.
    await asyncio.wait_for(process.communicate(), timeout)
