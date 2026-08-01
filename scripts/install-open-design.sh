#!/usr/bin/env bash
# Installs the Open Design MCP server and wires it into this coding agent.
# See docs/open-design.md for details.
set -euo pipefail

if ! command -v od >/dev/null 2>&1; then
  echo "The 'od' CLI was not found."
  echo "Install Open Design first: https://open-design.ai (desktop app) or via Docker (see docs/open-design.md)."
  exit 1
fi

od mcp install claude-code
