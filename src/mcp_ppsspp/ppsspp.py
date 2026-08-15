"""WebSocket client for PPSSPP's built-in debugger interface.

PPSSPP exposes a JSON-RPC-ish API over WebSocket when "Allow remote
debugger" is enabled in Settings -> Tools -> Developer Tools. Each
request is a JSON object with at least {"event": "<category.method>",
"ticket": "<correlation-id>"} plus method-specific params. The server
echoes the ticket back on the response so we can correlate.

Wire format (request):  {"event":"memory.read_u16","ticket":"t1","address":0x09000000}
Wire format (response): {"event":"memory.read_u16","ticket":"t1","uintValue":42}
Error response shape:   {"event":"error","ticket":"t1","message":"..."}

The server also pushes async broadcasts (events without tickets) for
things like game-state changes, stepping events, and log lines. We
route ticketed responses back to their pending requests and ignore
broadcasts for now.

Connection URL: ws://<host>:<port>/debugger
Subprotocol:    "debugger.ppsspp.org"

Port defaults: PPSSPP's WebSocket shares the disc-sharing port, which
is dynamic per install. The address shows in Settings -> Tools ->
Developer Tools -> Allow remote debugger. We accept it via env var
(PPSSPP_HOST, PPSSPP_PORT) - no sensible auto-discovery default since
the user has to opt in to enabling the debugger anyway.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any, Callable, Dict, Optional

import websockets
from websockets.asyncio.client import ClientConnection

__all__ = ["PpssppClient", "PpssppError"]


class PpssppError(Exception):
    """Raised for protocol-level failures surfaced to MCP tool calls."""


class PpssppClient:
    """Async WebSocket client speaking PPSSPP's ``debugger.ppsspp.org`` protocol.

    Auto-reconnects on demand: every ``call()`` / ``fire_and_forget()`` goes
    through ``ensure_connected()``, so PPSSPP can be launched, closed and
    relaunched at any point during the MCP server's lifetime without needing
    an MCP-client restart.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        timeout_ms: int = 10000,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout_ms = timeout_ms
        if not self._port:
            raise PpssppError(
                "PPSSPP_PORT must be set - see Settings -> Tools -> Developer "
                "Tools in PPSSPP for the active port."
            )

        self._ws: Optional[ClientConnection] = None
        self._recv_task: Optional[asyncio.Task[None]] = None
        # Requests sent and awaiting a ticketed reply, keyed by ticket.
        self._inflight: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._next_ticket = 1
        # True once the WebSocket reaches OPEN state.
        self._ready = False
        # Memoized connection lifecycle future - resolves once `_ready` is True.
        self._ready_promise: Optional[asyncio.Task[None]] = None
        self._stopping = False

    # ------------------------------------------------------------------ info

    def describe_target(self) -> str:
        return (
            f"ws://{self._host}:{self._port}/debugger "
            "(subprotocol: debugger.ppsspp.org)"
        )

    def is_connected(self) -> bool:
        return self._ws is not None and self._ready

    # --------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Connect to PPSSPP's debugger WebSocket.

        Resolves when the socket is open and ready for RPC. Rejects on
        connection failure or protocol handshake error.

        Memoized by ``_ready_promise`` so concurrent callers share a single
        underlying connect attempt. On failure the memo is cleared so the
        NEXT call can retry; the receive loop clears it again whenever the
        socket closes, so a later call reconnects instead of sending on a
        dead socket.
        """
        if self._ready_promise is not None:
            return await asyncio.shield(self._ready_promise)
        task = asyncio.create_task(self._connect())
        self._ready_promise = task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Only the current caller was cancelled - let the shared connect
            # attempt keep running for whoever else is waiting on it.
            raise
        except Exception:
            # _connect cleared _ready_promise before raising, so the next
            # start() attempt actually reconnects.
            raise

    async def _connect(self) -> None:
        self._stopping = False
        url = f"ws://{self._host}:{self._port}/debugger"
        try:
            ws = await websockets.connect(url, subprotocols=["debugger.ppsspp.org"])
        except Exception as err:
            self._ready_promise = None
            self._ws = None
            raise PpssppError(f"PPSSPP WebSocket connection failed: {err}") from err
        self._ws = ws
        self._ready = True
        print(f"[mcp-ppsspp] connected to {url}", file=sys.stderr)
        self._recv_task = asyncio.create_task(self._recv_loop(ws))

    async def stop(self) -> None:
        """Close the WebSocket and cancel the receive loop. Idempotent."""
        self._stopping = True
        self._ready = False
        connect_task = self._ready_promise
        self._ready_promise = None
        ws = self._ws
        self._ws = None
        recv_task = self._recv_task
        self._recv_task = None
        # Cancel an in-flight connect attempt (start() may still be dialing
        # when shutdown begins) so it can't complete on a dead event loop.
        if connect_task is not None and not connect_task.done():
            connect_task.cancel()
            try:
                await connect_task
            except (asyncio.CancelledError, Exception):
                pass
        if recv_task is not None:
            recv_task.cancel()
            try:
                await recv_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    # ------------------------------------------------------------- message IO

    async def _recv_loop(self, ws: ClientConnection) -> None:
        try:
            async for raw in ws:
                self._on_message(raw)
        except websockets.ConnectionClosed as exc:
            if not self._stopping:
                print(
                    f"[mcp-ppsspp] socket closed (code={exc.code}): {exc.reason}",
                    file=sys.stderr,
                )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # pragma: no cover - defensive
            if not self._stopping:
                print(f"[mcp-ppsspp] socket error: {err}", file=sys.stderr)
        finally:
            # CRITICAL: clear readyPromise too. start() short-circuits on a
            # truthy readyPromise - if we leave the old resolved task in place
            # after PPSSPP closes the socket, the NEXT ensure_connected()
            # returns that stale promise immediately and skips reconnecting,
            # leading to a send() on a None socket. Clearing it here forces
            # start() to actually attempt a fresh connect next time.
            self._ready = False
            self._ws = None
            self._ready_promise = None
            # Fail all in-flight requests.
            for fut in self._inflight.values():
                if not fut.done():
                    fut.set_exception(PpssppError("PPSSPP WebSocket closed mid-request"))
            self._inflight.clear()

    def _on_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as err:
            print(f"[mcp-ppsspp] bad JSON from PPSSPP: {err}", file=sys.stderr)
            return
        ticket = msg.get("ticket")
        if not ticket:
            # Async broadcast - game state change, log line, stepping event, etc.
            if os.environ.get("MCP_PPSSPP_DEBUG"):
                print(f"[trace] broadcast: {raw[:200]}", file=sys.stderr)
            return
        fut = self._inflight.get(ticket)
        if fut is None:
            print(f"[mcp-ppsspp] unknown ticket from PPSSPP: {ticket}", file=sys.stderr)
            return
        del self._inflight[ticket]
        if msg.get("event") == "error":
            if not fut.done():
                fut.set_exception(
                    PpssppError(f"PPSSPP error: {msg.get('message', '(no message)')}")
                )
        else:
            if not fut.done():
                fut.set_result(msg)

    # ------------------------------------------------------------------ RPC

    async def ensure_connected(self) -> None:
        """Lazy connection guard used by ``call()`` and ``fire_and_forget()``.

        If the socket isn't currently open, attempts to (re)connect, throwing a
        tool-call-shaped error on failure that points at the right fix.
        """
        if self.is_connected():
            return
        try:
            await self.start()
        except Exception as err:
            raise PpssppError(
                f"PPSSPP not reachable at {self.describe_target()}: {err}.  "
                'Make sure PPSSPP is running with "Allow remote debugger" enabled in '
                "Settings -> Tools -> Developer Tools."
            ) from err

    async def fire_and_forget(
        self, event: str, params: Optional[Dict[str, Any]] = None
    ) -> None:
        """Send a request with no ticketed reply expected.

        Used for events PPSSPP documents as having "no immediate response"
        (e.g. ``cpu.stepping``, ``cpu.resume``). Those ack via an async
        broadcast (no ticket) some time later; ``call()`` would hang waiting
        for a ticketed reply that never arrives. Callers usually follow this
        with ``wait_for_state()`` polling on ``cpu.status``.
        """
        await self.ensure_connected()
        msg = {"event": event, **(params or {})}
        if os.environ.get("MCP_PPSSPP_DEBUG"):
            print(f"[trace] TX (fire&forget): {json.dumps(msg)}", file=sys.stderr)
        assert self._ws is not None
        await self._ws.send(json.dumps(msg))

    async def call(
        self, event: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Send a request and wait for the ticketed response.

        ``event`` is the PPSSPP method name (e.g. ``memory.read_u16``).
        ``params`` is merged into the request object alongside ``event`` and
        ``ticket``. The returned dict is the FULL response (including ``event``
        and ``ticket`` fields); callers usually want a specific field like
        ``value`` or ``base64``.
        """
        await self.ensure_connected()
        ticket = f"t{self._next_ticket}"
        self._next_ticket += 1
        msg = {"event": event, "ticket": ticket, **(params or {})}
        if os.environ.get("MCP_PPSSPP_DEBUG"):
            print(f"[trace] TX: {json.dumps(msg)}", file=sys.stderr)
        fut: asyncio.Future[Dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._inflight[ticket] = fut
        assert self._ws is not None
        await self._ws.send(json.dumps(msg))
        try:
            return await asyncio.wait_for(fut, timeout=self._timeout_ms / 1000)
        except asyncio.TimeoutError:
            self._inflight.pop(ticket, None)
            raise PpssppError(
                f'PPSSPP call "{event}" timed out ({self._timeout_ms}ms) - '
                'is PPSSPP running with "Allow remote debugger" enabled?'
            ) from None

    async def wait_for_state(
        self,
        predicate: Callable[[Dict[str, Any]], bool],
        timeout_ms: int = 3000,
        interval_ms: int = 50,
    ) -> None:
        """Poll ``cpu.status`` (which IS synchronous) until ``predicate`` holds.

        Used to detect when fire-and-forget commands (``cpu.stepping`` /
        ``cpu.resume``) have actually taken effect, since they don't
        ticket-reply.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            status = await self.call("cpu.status")
            if predicate(status):
                return
            await asyncio.sleep(interval_ms / 1000)
        raise PpssppError(f"waitForState timed out after {timeout_ms}ms")
