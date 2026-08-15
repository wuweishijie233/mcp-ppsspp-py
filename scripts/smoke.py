#!/usr/bin/env python3
"""One-shot smoke test for mcp-ppsspp.

Usage: PPSSPP_PORT=<port> python scripts/smoke.py

What it does:
  1. Connect to PPSSPP's WebSocket debugger
  2. ping (version)
  3. game.status (game info + run state)
  4. Read a known PSP RAM byte
  5. Capture a screenshot to C:/temp/ppsspp-smoke.png
  6. List breakpoints (should be empty)
  7. Exit cleanly

Prereqs:
  - PPSSPP running with "Allow remote debugger" enabled
    (Settings -> Tools -> Developer Tools)
  - The PORT shown in PPSSPP's developer tools dialog, passed as env var
  - Any PSP ISO/EBOOT loaded (so screenshot has content)
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
from pathlib import Path

# Allow running straight from a source checkout without installing.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mcp_ppsspp.ppsspp import PpssppClient  # noqa: E402


def _screenshot_b64(r: dict) -> str | None:
    b64 = r.get("base64")
    if not b64 and r.get("uri"):
        m = re.match(r"^data:image/png;base64,(.*)$", r["uri"])
        if m:
            b64 = m.group(1)
    return b64


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
            print(f"FAIL: connection - {err}", file=sys.stderr)
            return 1
        print("  connected.\n")

        # 1. ping (version)
        print("=== ping (version) ===")
        v = await pp.call("version")
        print("  " + json.dumps(v, indent=2))

        # 2. game.status
        print("\n=== game.status ===")
        status = await pp.call("game.status")
        print("  " + json.dumps(status, indent=2))

        # 3. memory.read_u8 - try user RAM base (game start often near here)
        print("\n=== memory.read_u8 @ 0x08800000 (user RAM base) ===")
        try:
            r = await pp.call("memory.read_u8", {"address": 0x08800000})
            print("  " + json.dumps(r))
        except Exception as e:
            print(f"  (skipped - {e})")

        # 4. memory.read (range) - try a small block of user RAM
        print("\n=== memory.read 16 bytes @ 0x08800000 ===")
        try:
            r = await pp.call("memory.read", {"address": 0x08800000, "size": 16})
            if r.get("base64"):
                data = base64.b64decode(r["base64"])
                print("  " + " ".join(f"{b:02x}" for b in data))
            else:
                print("  (no base64 in response: " + json.dumps(r) + ")")
        except Exception as e:
            print(f"  (skipped - {e})")

        # 5. screenshot - must pause first (gpu.buffer.screenshot requires stepping)
        print("\n=== gpu.buffer.screenshot (with auto-pause/resume) ===")
        try:
            await pp.fire_and_forget("cpu.stepping")
            await pp.wait_for_state(lambda s: s.get("stepping") is True)
            try:
                r = await pp.call("gpu.buffer.screenshot", {"type": "base64"})
                b64 = _screenshot_b64(r)
                if b64:
                    out = Path(os.environ.get("PPSSPP_SMOKE_OUT", "C:/temp/ppsspp-smoke.png"))
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(base64.b64decode(b64))
                    print(f"  saved to {out} ({len(base64.b64decode(b64))} bytes decoded)")
                else:
                    print("  (no screenshot data: " + json.dumps(r)[:200] + ")")
            finally:
                try:
                    await pp.fire_and_forget("cpu.resume")
                    await pp.wait_for_state(lambda s: s.get("stepping") is False, timeout_ms=2000)
                except Exception:
                    pass
        except Exception as e:
            print(f"  (skipped - {e})")

        # 5b. get all registers
        print("\n=== cpu.getAllRegs (first category only) ===")
        try:
            await pp.fire_and_forget("cpu.stepping")
            await pp.wait_for_state(lambda s: s.get("stepping") is True)
            try:
                r = await pp.call("cpu.getAllRegs")
                cats = r.get("categories") or []
                if cats:
                    c0 = cats[0]
                    names = c0.get("registerNames") or []
                    vals = c0.get("uintValues") or []
                    print(f"  {c0.get('name', '')}: {len(names)} registers")
                    for i in range(min(8, len(names))):
                        print(f"    {names[i]:<8} = 0x{(vals[i] if i < len(vals) else 0):08X}")
                    if len(names) > 8:
                        print(f"    ... (+{len(names) - 8} more)")
                else:
                    print("  (no categories)")
            finally:
                try:
                    await pp.fire_and_forget("cpu.resume")
                    await pp.wait_for_state(lambda s: s.get("stepping") is False, timeout_ms=2000)
                except Exception:
                    pass
        except Exception as e:
            print(f"  (skipped - {e})")

        # 5c. memory write round-trip (write a sentinel, read it back, confirm)
        print("\n=== memory.write_u32 + read_u32 round-trip ===")
        try:
            test_addr = 0x08800000
            test_val = 0xDEADBEEF
            await pp.call("memory.write_u32", {"address": test_addr, "value": test_val})
            r = await pp.call("memory.read_u32", {"address": test_addr})
            if r.get("value") == test_val:
                print(f"  wrote 0x{test_val:X} -> 0x{test_addr:X}, read back OK")
            else:
                print(f"  MISMATCH - wrote 0x{test_val:X}, read 0x{(r.get('value') or 0):X}")
        except Exception as e:
            print(f"  (skipped - {e})")

        # 6. breakpoint list
        print("\n=== cpu.breakpoint.list ===")
        try:
            r = await pp.call("cpu.breakpoint.list")
            print("  " + json.dumps(r))
        except Exception as e:
            print(f"  (skipped - {e})")

        print("\n=== smoke complete ===")
        return 0
    finally:
        await pp.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
