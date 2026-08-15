"""In-process fake PPSSPP WebSocket debugger server used by the test suite.

Implements just enough of PPSSPP's ``debugger.ppsspp.org`` protocol to
exercise the client: ticket correlation, error responses, fire-and-forget
events (cpu.stepping / cpu.resume), cpu.status polling, and the reconnect
path.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, Callable, Dict, Optional, Set

import websockets
from websockets.asyncio.server import Server, ServerConnection

# A 1x1 transparent PNG, base64-encoded.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class FakePPSSPP:
    """Minimal PPSSPP debugger: ticket echo + per-event handlers.

    Handlers receive the full request dict and return either a dict
    (merged into the ticketed response) or ``None`` (fire-and-forget:
    no ticketed reply, mirroring cpu.stepping / cpu.resume).
    """

    def __init__(self) -> None:
        self._stepping = False
        self._connections: Set[ServerConnection] = set()
        self._server: Optional[Server] = None
        self.requests: list[Dict[str, Any]] = []
        self.handlers: Dict[str, Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = {
            "version": lambda m: {"name": "PPSSPP", "version": "1.20.3"},
            "game.status": lambda m: {
                "game": {"id": "ULUS10500", "title": "Fake Game", "version": "1.0"},
                "paused": self._stepping,
                "stepping": self._stepping,
            },
            "cpu.status": lambda m: {
                "paused": self._stepping,
                "stepping": self._stepping,
                "pc": 0x08800100,
                "ticks": 0,
            },
            "cpu.stepping": self._set_stepping(True),
            "cpu.resume": self._set_stepping(False),
            "cpu.stepInto": lambda m: {"pc": 0x08800104},
            "cpu.getAllRegs": lambda m: {
                "categories": [
                    {
                        "name": "GPR",
                        "registerNames": ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3"],
                        "uintValues": [0, 0, 1, 2, 0x08800000, 0, 0, 0],
                        "floatValues": ["0.0", "0.0", "1.0", "2.0", "0.0", "0.0", "0.0", "0.0"],
                    }
                ]
            },
            "cpu.setReg": lambda m: {"uintValue": m.get("value", 0)},
            "cpu.breakpoint.add": lambda m: {},
            "cpu.breakpoint.remove": lambda m: {},
            "cpu.breakpoint.list": lambda m: {"breakpoints": []},
            "memory.read_u8": lambda m: {"value": 0x42},
            "memory.read_u16": lambda m: {"value": 0xBEEF},
            "memory.read_u32": lambda m: {"value": 0xDEADBEEF},
            "memory.read": lambda m: {"base64": base64.b64encode(bytes(range(min(m.get("size", 16), 16)))).decode("ascii")},
            "memory.readString": lambda m: {"value": "hello psp"},
            "memory.write_u8": lambda m: {},
            "memory.write_u16": lambda m: {},
            "memory.write_u32": lambda m: {},
            "memory.write": lambda m: {},
            "input.buttons.send": lambda m: {},
            "input.buttons.press": lambda m: {},
            "input.analog.send": lambda m: {},
            "game.reset": lambda m: {},
            "gpu.buffer.renderColor": lambda m: {"base64": TINY_PNG_B64},
            "gpu.buffer.screenshot": lambda m: {"base64": TINY_PNG_B64},
            # Handlers registered below return None -> no ticketed reply.
            "never.reply": lambda m: None,
        }

    def _set_stepping(self, value: bool) -> Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]:
        def handler(m: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            self._stepping = value
            return None  # fire-and-forget: PPSSPP acks via async broadcast only
        return handler

    async def start(self) -> int:
        """Start the server on an ephemeral port and return the port."""
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0, subprotocols=["debugger.ppsspp.org"])
        assert self._server.sockets is not None
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        await self.close_all()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def close_all(self) -> None:
        """Drop every client connection (simulates PPSSPP closing the socket)."""
        for ws in list(self._connections):
            try:
                await ws.close()
            except Exception:
                pass

    async def _handler(self, ws: ServerConnection) -> None:
        self._connections.add(ws)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self.requests.append(msg)
                event = msg.get("event")
                handler = self.handlers.get(event)
                ticket = msg.get("ticket")
                if handler is None:
                    if ticket:
                        await ws.send(json.dumps({"event": "error", "ticket": ticket, "message": f"unknown event {event}"}))
                    continue
                result = handler(msg)
                if result is None or not ticket:
                    continue
                await ws.send(json.dumps({"event": event, "ticket": ticket, **result}))
        finally:
            self._connections.discard(ws)
