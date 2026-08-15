# scripts/

Verification scripts for development. Each runs against a live PPSSPP instance
with the debugger enabled.

## Files

- **`smoke.py`** - basic liveness probe: ping, get_info, read a few bytes.
  Quickest "is everything wired up?" check.
- **`verify_reconnect.py`** - closes the WebSocket under the client to
  confirm reconnect logic survives. Regression guard for the WebSocket
  reconnect path.
- **`verify_screenshot.py`** - captures a framebuffer and validates the PNG
  is non-empty and decodable. Regression guard for screenshot tool.

## Usage

```bash
PPSSPP_PORT=12345 python scripts/smoke.py
PPSSPP_PORT=12345 python scripts/verify_reconnect.py
PPSSPP_PORT=12345 python scripts/verify_screenshot.py
```

Set `PPSSPP_PORT` to whatever PPSSPP's Developer Tools dialog shows.
