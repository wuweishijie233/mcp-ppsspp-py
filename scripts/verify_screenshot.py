#!/usr/bin/env python3
"""Verify the screenshot tool's two capture paths against a live PPSSPP.

A failure here means a real regression - the script reports it as such,
not a script bug.

Usage: PPSSPP_PORT=<port> python scripts/verify_screenshot.py

Prereqs:
  - PPSSPP running with "Allow remote debugger" enabled
    (Settings -> Tools -> Developer Tools)
  - A PSP game loaded (so the framebuffers have content to read)
  - The port from the developer tools dialog passed via env

Outputs:
  - C:/temp/ppsspp-verify-render.png  (from gpu.buffer.renderColor)
  - C:/temp/ppsspp-verify-output.png  (from gpu.buffer.screenshot, if it
                                       didn't crash)

Exit codes:
  0 - render path works (the default; this is the main thing v0.1.3 ships)
  1 - render path failed (regression - investigate)
  2 - PPSSPP not reachable / no port
Note: 'output' path failure does NOT cause non-zero exit; it's expected to
be fragile per the v0.1.3 known-limitation note.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mcp_ppsspp.ppsspp import PpssppClient  # noqa: E402

OUT_DIR = Path(os.environ.get("PPSSPP_VERIFY_OUT", "C:/temp"))


async def capture_with_source(pp: PpssppClient, event: str, label: str, out_path: Path) -> bool:
    print(f"\n=== {label}: {event} ===")
    status_before = await pp.call("cpu.status")
    was_stepping = bool(status_before.get("stepping"))

    if not was_stepping:
        await pp.fire_and_forget("cpu.stepping")
        await pp.wait_for_state(lambda s: s.get("stepping") is True)

    ok = False
    try:
        r = await pp.call(event, {"type": "base64"})
        b64 = r.get("base64")
        if not b64 and r.get("uri"):
            m = re.match(r"^data:image/png;base64,(.*)$", r["uri"])
            if m:
                b64 = m.group(1)
        if b64:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(base64.b64decode(b64))
            print(f"  PASS - saved {out_path} ({len(base64.b64decode(b64))} bytes decoded)")
            ok = True
        else:
            print(f"  FAIL - no image data: {str(r)[:200]}")
    except Exception as err:
        # The interesting failure mode for 'output' is the WebSocket closing
        # mid-request - PpssppClient surfaces that as "PPSSPP WebSocket closed
        # mid-request". That means PPSSPP itself crashed (upstream #21683).
        if "closed mid-request" in str(err):
            print("  CRASH - PPSSPP process died (upstream bug #21683 fired).")
            print("          MCP server will auto-reconnect on next call (v0.1.2 fix).")
        else:
            print(f"  FAIL - {err}")

    if not was_stepping:
        try:
            await pp.fire_and_forget("cpu.resume")
            await pp.wait_for_state(lambda s: s.get("stepping") is False, timeout_ms=2000)
        except Exception:
            pass  # best-effort; if PPSSPP died, this throws too

    return ok


async def main() -> int:
    port = int(os.environ.get("PPSSPP_PORT", "0") or "0")
    host = os.environ.get("PPSSPP_HOST", "127.0.0.1")
    if not port:
        print(
            "PPSSPP_PORT not set. See Settings -> Tools -> Developer Tools in PPSSPP for the active port.",
            file=sys.stderr,
        )
        return 2

    pp = PpssppClient(host=host, port=port)
    try:
        print(f"=== connecting to {pp.describe_target()} ===")
        try:
            await pp.start()
        except Exception as err:
            print(f"FAIL: connection - {err}")
            return 2
        print("  connected.")

        # Confirm a game is loaded - both readback paths need framebuffer content.
        status = await pp.call("game.status")
        game = status.get("game")
        if not game:
            print("\nWARN: no game loaded. Load a PSP game in PPSSPP, then re-run.")
            print("      Without a game, both sources will return 'no image data'.")
        else:
            print(f"  game loaded: {game.get('title', '(untitled)')} ({game.get('id', '?')})")

        # Test 1: the new default - gpu.buffer.renderColor.
        # This is what ppsspp_screenshot uses now without an explicit source param.
        render_ok = await capture_with_source(
            pp,
            "gpu.buffer.renderColor",
            "render source (default in v0.1.3)",
            OUT_DIR / "ppsspp-verify-render.png",
        )

        # Test 2: the opt-in path - gpu.buffer.screenshot.
        # This is what ppsspp_screenshot uses with source: 'output'.
        # Expected to either work cleanly OR crash PPSSPP (upstream #21683).
        output_ok = await capture_with_source(
            pp,
            "gpu.buffer.screenshot",
            "output source (opt-in, can crash PPSSPP)",
            OUT_DIR / "ppsspp-verify-output.png",
        )

        print("\n=== summary ===")
        print(f"  render (default):       {'PASS' if render_ok else 'FAIL'}")
        print(f"  output (opt-in):        {'PASS' if output_ok else 'FAIL/CRASH (per upstream #21683)'}")

        # Only the render path failing is a hard regression - that's the default,
        # and v0.1.3's whole point is that it works reliably.
        return 0 if render_ok else 1
    finally:
        await pp.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
