"""stdio MCP entrypoint for mcp-ppsspp.

Reads ``PPSSPP_HOST`` / ``PPSSPP_PORT`` (port required, no default), opens
the WebSocket to PPSSPP's debugger, registers every tool, and serves MCP
requests over stdio.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP

from mcp_ppsspp.ppsspp import PpssppClient
from mcp_ppsspp.tools import register_tools

HOST = os.environ.get("PPSSPP_HOST", "127.0.0.1")
PORT = int(os.environ.get("PPSSPP_PORT", "0") or "0")

FATAL_MSG = (
    "[mcp-ppsspp] FATAL: PPSSPP_PORT not set.\n"
    "             PPSSPP's debugger WebSocket shares the disc-sharing port, which is dynamic.\n"
    "             1. In PPSSPP: Settings > Tools > Developer Tools > Allow remote debugger\n"
    "             2. The active address (e.g. ws://192.168.1.10:12345/debugger) will be shown\n"
    "             3. Set PPSSPP_PORT (and PPSSPP_HOST if not localhost) and restart this server\n"
)


async def _early_connect(pp: PpssppClient) -> None:
    """Best-effort connect at startup so we fail fast with a clear error.

    Never fail-stops the MCP transport: the server keeps serving
    tools/list over stdio even if PPSSPP is down, and individual tool
    calls attempt their own lazy (re)connect.
    """
    try:
        await pp.start()
    except Exception as err:
        print(
            f"[mcp-ppsspp] could not reach PPSSPP at {pp.describe_target()}: {err}\n"
            "             Server still serving tools/list over stdio. Tool calls will fail until PPSSPP is up.",
            file=sys.stderr,
        )


def build_server(pp: PpssppClient) -> FastMCP:
    """Build the FastMCP server with every PPSSPP tool registered."""

    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[None]:
        task = asyncio.create_task(_early_connect(pp))
        try:
            yield
        finally:
            task.cancel()
            await pp.stop()

    mcp = FastMCP("mcp-ppsspp", lifespan=lifespan)
    register_tools(mcp, pp)
    return mcp


def main() -> int:
    if not PORT:
        sys.stderr.write(FATAL_MSG)
        return 1
    pp = PpssppClient(host=HOST, port=PORT)
    mcp = build_server(pp)
    # Blocks until the stdio transport ends (MCP client disconnects / stdin
    # closes), then FastMCP tears the event loop down cleanly.
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
