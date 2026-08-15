"""End-to-end stdio test: spawn the real server and talk to it via the MCP client."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from fake_ppsspp import FakePPSSPP  # noqa: E402


async def test_stdio_server_lists_and_calls_tools():
    server = FakePPSSPP()
    port = await server.start()
    env = {
        **os.environ,
        "PPSSPP_HOST": "127.0.0.1",
        "PPSSPP_PORT": str(port),
        "PYTHONPATH": str(_SRC) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_ppsspp"],
        cwd=str(ROOT),
        env=env,
    )
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "mcp-ppsspp"

                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                assert "ppsspp_ping" in names
                assert "ppsspp_screenshot" in names
                assert len(names) == 24

                res = await session.call_tool("ppsspp_ping", {})
                assert res.content[0].text == "pong (PPSSPP 1.20.3)"

                res = await session.call_tool("ppsspp_read8", {"address": 0x08800000})
                assert res.content[0].text == "0x08800000: 66 (0x42)"
    finally:
        await server.stop()


async def test_stdio_server_fails_cleanly_without_port():
    env = {
        **os.environ,
        "PPSSPP_PORT": "",
        "PYTHONPATH": str(_SRC) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_ppsspp"],
        cwd=str(ROOT),
        env=env,
    )
    with pytest.raises(Exception):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
