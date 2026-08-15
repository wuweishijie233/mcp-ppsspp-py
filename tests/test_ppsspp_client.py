"""Tests for the PpssppClient WebSocket protocol implementation."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mcp_ppsspp.ppsspp import PpssppClient, PpssppError  # noqa: E402
from fake_ppsspp import FakePPSSPP  # noqa: E402


@pytest.fixture
async def fake():
    server = FakePPSSPP()
    port = await server.start()
    yield server, port
    await server.stop()


@pytest.fixture
async def client(fake):
    server, port = fake
    c = PpssppClient(host="127.0.0.1", port=port, timeout_ms=2000)
    yield c
    await c.stop()


async def test_call_returns_ticketed_response(client):
    r = await client.call("version")
    assert r["name"] == "PPSSPP"
    assert r["version"] == "1.20.3"


async def test_call_correlates_concurrent_requests(client):
    # Fire several calls without awaiting between sends; the fake echoes the
    # ticket so each must land on the right future.
    results = await asyncio.gather(
        client.call("memory.read_u8", {"address": 0x08800000}),
        client.call("memory.read_u16", {"address": 0x08800000}),
        client.call("memory.read_u32", {"address": 0x08800000}),
        client.call("version"),
    )
    assert results[0]["value"] == 0x42
    assert results[1]["value"] == 0xBEEF
    assert results[2]["value"] == 0xDEADBEEF
    assert results[3]["version"] == "1.20.3"


async def test_error_response_raises(client, fake):
    server, port = fake
    with pytest.raises(PpssppError, match="unknown event"):
        await client.call("no.such.event")
    assert server.requests[-1]["event"] == "no.such.event"


async def test_timeout(fake):
    server, port = fake
    c = PpssppClient(host="127.0.0.1", port=port, timeout_ms=200)
    try:
        with pytest.raises(PpssppError, match="timed out"):
            await c.call("never.reply")
    finally:
        await c.stop()


async def test_fire_and_forget_then_wait_for_state(client, fake):
    server, port = fake
    await client.fire_and_forget("cpu.stepping")
    await client.wait_for_state(lambda s: s.get("stepping") is True, timeout_ms=1000)
    assert server._stepping is True

    await client.fire_and_forget("cpu.resume")
    await client.wait_for_state(lambda s: s.get("stepping") is False, timeout_ms=1000)
    assert server._stepping is False


async def test_reconnect_after_socket_close(client, fake):
    server, port = fake
    assert (await client.call("version"))["version"] == "1.20.3"

    # Simulate PPSSPP closing the socket mid-session.
    await server.close_all()
    deadline = asyncio.get_running_loop().time() + 3
    while client.is_connected() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)

    # The next call must transparently reconnect on a fresh socket.
    r = await client.call("version")
    assert r["version"] == "1.20.3"
    assert client.is_connected()


async def test_port_required():
    with pytest.raises(PpssppError, match="PPSSPP_PORT must be set"):
        PpssppClient(port=0)
