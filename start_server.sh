#!/usr/bin/env bash
export MCP_TRANSPORT=streamable-http
export FASTMCP_HOST=0.0.0.0
export FASTMCP_PORT=8000
uv run --directory "$(dirname "$0")" yfmcp
