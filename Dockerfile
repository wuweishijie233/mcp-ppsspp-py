# Dockerfile - primarily for the Glama MCP registry (https://glama.ai/mcp/servers).
#
# Builds the MCP server and runs it over stdio. The server starts cleanly
# WITHOUT PPSSPP present: it still serves tools/list over stdio. That's
# exactly what Glama's "start + respond to introspection" check needs.
#
# For actual use you don't need Docker - `pip install mcp-ppsspp` and point
# a running PPSSPP at it via the PPSSPP_PORT env var. See README.md.

FROM python:3.12-slim
WORKDIR /app

# Install the package (and its runtime deps: mcp, websockets).
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# The MCP server speaks JSON-RPC over stdio.
ENTRYPOINT ["mcp-ppsspp"]
