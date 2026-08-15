"""Tests for MCP tool registration and behavior against the fake PPSSPP."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mcp_ppsspp.ppsspp import PpssppClient  # noqa: E402
from mcp_ppsspp.tools import PSP_BUTTONS, register_tools  # noqa: E402
from fake_ppsspp import FakePPSSPP  # noqa: E402

EXPECTED_TOOLS = [
    "ppsspp_ping",
    "ppsspp_get_info",
    "ppsspp_read8",
    "ppsspp_read16",
    "ppsspp_read32",
    "ppsspp_read_range",
    "ppsspp_read_string",
    "ppsspp_write8",
    "ppsspp_write16",
    "ppsspp_write32",
    "ppsspp_write_range",
    "ppsspp_press_buttons",
    "ppsspp_press_button",
    "ppsspp_send_analog",
    "ppsspp_pause",
    "ppsspp_resume",
    "ppsspp_step",
    "ppsspp_reset",
    "ppsspp_screenshot",
    "ppsspp_get_registers",
    "ppsspp_set_register",
    "ppsspp_breakpoint_add",
    "ppsspp_breakpoint_remove",
    "ppsspp_breakpoint_list",
]


@pytest.fixture
async def env():
    server = FakePPSSPP()
    port = await server.start()
    client = PpssppClient(host="127.0.0.1", port=port, timeout_ms=2000)
    mcp = FastMCP("test")
    register_tools(mcp, client)
    yield server, client, mcp
    await client.stop()
    await server.stop()


async def test_all_tools_registered(env):
    _, _, mcp = env
    tools = await mcp.list_tools()
    assert [t.name for t in tools] == EXPECTED_TOOLS
    for t in tools:
        assert t.description.startswith("PURPOSE:")


async def test_read8_schema(env):
    _, _, mcp = env
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["ppsspp_read8"].inputSchema
    assert schema["required"] == ["address"]
    assert schema["properties"]["address"]["type"] == "integer"
    assert schema["properties"]["address"]["minimum"] == 0


async def test_write32_schema(env):
    _, _, mcp = env
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["ppsspp_write32"].inputSchema
    assert schema["required"] == ["address", "value"]
    assert schema["properties"]["value"]["maximum"] == 4294967295


async def test_buttons_schema(env):
    _, _, mcp = env
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["ppsspp_press_buttons"].inputSchema
    props = schema["properties"]["buttons"]
    assert props["type"] == "object"
    assert props["additionalProperties"] == {"type": "boolean"}


async def test_send_analog_schema(env):
    _, _, mcp = env
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["ppsspp_send_analog"].inputSchema
    assert schema["properties"]["stick"]["enum"] == ["left", "right"]
    assert schema["properties"]["x"]["maximum"] == 1


async def test_ping_tool(env):
    _, _, mcp = env
    result = await mcp.call_tool("ppsspp_ping", {})
    assert result[0].text == "pong (PPSSPP 1.20.3)"


async def test_get_info_tool(env):
    _, _, mcp = env
    result = await mcp.call_tool("ppsspp_get_info", {})
    text = result[0].text
    assert "Fake Game" in text
    assert "ULUS10500" in text
    assert "running" in text


async def test_read8_tool(env):
    _, _, mcp = env
    result = await mcp.call_tool("ppsspp_read8", {"address": 0x08800000})
    assert result[0].text == "0x08800000: 66 (0x42)"


async def test_read_range_tool(env):
    _, _, mcp = env
    result = await mcp.call_tool("ppsspp_read_range", {"address": 0x08800000, "size": 4})
    text = result[0].text
    assert text.startswith("0x08800000 [4 bytes]:")
    assert "00 01 02 03" in text


async def test_read_string_tool(env):
    _, _, mcp = env
    result = await mcp.call_tool("ppsspp_read_string", {"address": 0x08800000})
    assert result[0].text == '0x08800000: "hello psp"'


async def test_write_range_tool(env):
    _, _, mcp = env
    result = await mcp.call_tool("ppsspp_write_range", {"address": 0x08800000, "bytes": [1, 2, 3]})
    assert result[0].text == "Wrote 3 bytes -> 0x08800000"


async def test_press_buttons_tool(env):
    _, _, mcp = env
    result = await mcp.call_tool(
        "ppsspp_press_buttons",
        {"buttons": {"cross": True, "right": True, "start": False}},
    )
    assert result[0].text == "Set buttons: cross+right"


async def test_pause_resume_tools(env):
    server, _, mcp = env
    result = await mcp.call_tool("ppsspp_pause", {})
    assert result[0].text == "Emulation paused"
    assert server._stepping is True
    result = await mcp.call_tool("ppsspp_resume", {})
    assert result[0].text == "Emulation resumed"
    assert server._stepping is False


async def test_screenshot_tool_returns_image(env):
    _, _, mcp = env
    result = await mcp.call_tool("ppsspp_screenshot", {})
    assert result[0].text.startswith("Screenshot captured (source: render")
    assert result[1].type == "image"
    assert result[1].mimeType == "image/png"


async def test_get_registers_tool(env):
    _, _, mcp = env
    result = await mcp.call_tool("ppsspp_get_registers", {})
    text = result[0].text
    assert "-- GPR --" in text
    assert "v0" in text


async def test_set_register_tool(env):
    _, _, mcp = env
    result = await mcp.call_tool("ppsspp_set_register", {"register": "v0", "value": 0x1234})
    assert result[0].text == "Set v0 = 0x00001234"


async def test_breakpoint_list_tool(env):
    _, _, mcp = env
    result = await mcp.call_tool("ppsspp_breakpoint_list", {})
    assert result[0].text == "No breakpoints set."


async def test_unknown_tool_raises(env):
    _, _, mcp = env
    with pytest.raises(Exception):
        await mcp.call_tool("ppsspp_nope", {})
