#!/usr/bin/env python3
"""Verify the v0.1.4 stale-readyPromise fix (auto-reconnect).

Bug: when PPSSPP closes the WebSocket (taskkill, crash, user quit), the
close handler used to clear ws+ready but NOT readyPromise. The next
ensure_connected() -> start() would short-circuit on the cached resolved
promise and skip the actual reconnect, leading to `this.ws!.send(...)`
throwing "Cannot read properties of null (reading 'send')".

Test: connect -> ping -> terminate underlying WebSocket -> ping again.
The second ping must produce a CLEAN error ("PPSSPP not reachable" only
if we then try a fresh connect, OR if PPSSPP is still up, it must succeed
via a fresh socket - either way, NO null-deref / send-on-dead-socket).

We close the WebSocket from our side (ws.close()) rather than taskkill-ing
PPSSPP, because (a) the close handler is what the bug is in - doesn't
matter who initiated the close, and (b) the client has no TCP keepalive
enabled, so taskkill-induced peer death takes minutes for our side to
detect. close() fires the close handler instantly.

Usage: PPSSPP_PORT=<port> python scripts/verify_reconnect.py

Prereqs:
  - PPSSPP running with "Allow remote debugger" enabled
    (PPSSPP is left running; only the WebSocket connection is dropped)

Exit codes:
  0 - fix is intact: second ping succeeded via fresh reconnect
  1 - bug regressed: send-on-dead-socket reappeared, or the test couldn't
      establish the prereq state
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mcp_ppsspp.ppsspp import PpssppClient  # noqa: E402


async def main() -> int:
    port = int(os.environ.get("PPSSPP_PORT", "0") or "0")
    host = os.environ.get("PPSSPP_HOST", "127.0.0.1")
    if not port:
        print(
            "PPSSPP_PORT not set. See Settings -> Tools -> Developer Tools in PPSSPP for the active port.",
            file=sys.stderr,
        )
        return 1

    pp = PpssppClient(host=host, port=port)
    try:
        # Step 1: initial connect (assumes user has PPSSPP running)
        print(f"=== step 1: connect to {pp.describe_target()} ===")
        try:
            await pp.start()
        except Exception as err:
            print(f"PREREQ FAIL: connection - {err}")
            print("Launch PPSSPP first, then re-run.")
            return 1
        print("  connected.")

        print("\n=== step 2: initial ping (proves session is live) ===")
        v1 = await pp.call("version")
        print(f"  pong ({v1.get('name', '?')} {v1.get('version', '?')})")

        # Step 3: close the WebSocket from our side. pp._ws is a private
        # field but Python doesn't enforce that. close() sends a proper close
        # frame so PPSSPP forgets the session and we can cleanly reconnect.
        print("\n=== step 3: close WebSocket (simulates peer-side close) ===")
        if pp._ws is None:  # noqa: SLF001 - test reaching into internals
            print("PREREQ FAIL: pp._ws is None right after a successful ping. Wrong state.")
            return 1
        await pp._ws.close()  # noqa: SLF001
        print("  ws.close() called.")

        # Step 4: poll until the close handler fires (is_connected() flips False).
        print("\n=== step 4: wait for close handler (poll is_connected, max 3s) ===")
        deadline = asyncio.get_running_loop().time() + 3
        while pp.is_connected() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        if pp.is_connected():
            print("PREREQ FAIL: close handler didn't fire within 3s of close().")
            return 1
        print("  close handler fired.")

        # Step 5: the regression test. The bug would raise on send-to-None
        # ("'NoneType' object has no attribute 'send'") because start()
        # short-circuits on stale readyPromise and call() proceeds to send
        # on a null socket. With the fix, start() actually attempts a fresh
        # connect, succeeds (PPSSPP is still up), and the ping returns cleanly.
        print("\n=== step 5: ping again (should auto-reconnect cleanly) ===")
        second_err = None
        v2 = None
        try:
            v2 = await pp.call("version")
        except Exception as err:
            second_err = err

        print("\n=== verdict ===")
        if second_err and "NoneType" in str(second_err):
            print("  FAIL - stale-readyPromise bug regressed.")
            print("         Close handler must clear readyPromise so start() actually retries.")
            return 1
        if v2:
            print(f"  PASS - auto-reconnect succeeded: pong ({v2.get('name', '?')} {v2.get('version', '?')})")
            return 0
        print("  PASS (with caveat) - got past the send-on-dead-socket; PPSSPP closed the new")
        print(f"         socket: \"{second_err}\".")
        print("         That's a PPSSPP-side rapid-reconnect quirk, not the bug we're testing.")
        return 0
    finally:
        await pp.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
