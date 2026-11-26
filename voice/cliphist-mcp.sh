#!/usr/bin/env bash
# Starts the cliphist-android MCP server (clipboard history) for a voice turn; mcp.cliphist.json
# points here. The API token is read from a file and handed over in the environment, so it never
# appears in mcp.json, argv or the logs.
#
#   CLIPHIST_TOKEN_FILE  token file (default: voice/state/cliphist-token, never committed)
#   CLIPHIST_MCP         built server (default: /root/cliphist-android/mcp-server/dist/index.js)
#   CLIPHIST_API_URL     the app's local API (default: http://127.0.0.1:8765)
#
# The app redacts one-time codes, passwords and tokens before items leave it (cliphist-android
# SecretRedactor); item content is untrusted data either way.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
TOKEN_FILE=${CLIPHIST_TOKEN_FILE:-$HERE/state/cliphist-token}
SERVER=${CLIPHIST_MCP:-/root/cliphist-android/mcp-server/dist/index.js}
[ -s "$TOKEN_FILE" ] || { echo "cliphist-mcp: no token in $TOKEN_FILE" >&2; exit 2; }
[ -f "$SERVER" ] || { echo "cliphist-mcp: $SERVER not built (cd mcp-server && npm ci && npm run build)" >&2; exit 2; }
CLIPHIST_API_TOKEN=$(tr -d '\r\n' <"$TOKEN_FILE")
export CLIPHIST_API_TOKEN CLIPHIST_API_URL=${CLIPHIST_API_URL:-http://127.0.0.1:8765}
exec "${CLIPHIST_NODE:-node}" "$SERVER"
