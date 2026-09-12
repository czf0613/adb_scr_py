"""FastAPI-hosted Android MCP server (install the ``mcp`` extra)."""

from .app import create_app

__all__ = ["create_app"]
