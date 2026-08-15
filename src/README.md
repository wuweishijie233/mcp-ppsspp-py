# src/

Python source for the `mcp-ppsspp` MCP server. Installed into the environment
by `pip install -e .` (or `pip install .` for a non-editable build); the
`mcp-ppsspp` console script is what MCP clients launch.

## Files

- **`mcp_ppsspp/server.py`** - stdio MCP entrypoint. Reads `PPSSPP_HOST` /
  `PPSSPP_PORT` (port required, no default), kicks off a best-effort early
  connection to PPSSPP's debugger, registers every tool on the FastMCP
  server, and serves MCP requests over stdio.
- **`mcp_ppsspp/ppsspp.py`** - async WebSocket client speaking PPSSPP's
  `debugger.ppsspp.org` subprotocol. Translates each MCP tool call to a
  debugger JSON-RPC request. Auto-reconnects if PPSSPP restarts.
- **`mcp_ppsspp/tools.py`** - registers every MCP tool against the FastMCP
  server. PSP-specific: two press tools (`ppsspp_press_buttons` persistent
  vs. `ppsspp_press_button` auto-release), MIPS register access, CPU
  breakpoints.

## Build / test

```bash
pip install -e ".[dev]"
pytest
```

Run the server directly:

```bash
PPSSPP_PORT=12345 python -m mcp_ppsspp
```
