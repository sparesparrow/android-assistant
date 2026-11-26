#!/usr/bin/env bash
# Registers the cliphist-android MCP server (clipboard history) for normal Claude Code sessions
# on the phone (user scope). It runs through voice/cliphist-mcp.sh, so the token stays in its
# file and never reaches ~/.claude.json, argv or the output.
#
#   register-cliphist-mcp.sh           dry-run: show the checks and the command, change nothing
#   register-cliphist-mcp.sh --apply   register it (the owner's action, on the phone); idempotent
#
#   CLIPHIST_TOKEN_FILE  token file (default: voice/state/cliphist-token, never committed)
#   CLIPHIST_MCP         built server (default: /root/cliphist-android/mcp-server/dist/index.js)
#
# The server also exposes add_to_clipboard: keep it out of allowedTools and deny it. Clipboard
# item content is untrusted data.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WRAPPER=$ROOT/voice/cliphist-mcp.sh
TOKEN_FILE=${CLIPHIST_TOKEN_FILE:-$ROOT/voice/state/cliphist-token}
SERVER=${CLIPHIST_MCP:-/root/cliphist-android/mcp-server/dist/index.js}
CMD=(claude mcp add --scope user cliphist -- "$WRAPPER")

case ${1:-} in
  "")
    if [ -s "$TOKEN_FILE" ]; then echo "token: $TOKEN_FILE (non-empty)"; else echo "token: none in $TOKEN_FILE (--apply refuses)"; fi
    if [ -f "$SERVER" ]; then echo "server: $SERVER"; else echo "server: $SERVER not built (--apply refuses)"; fi
    echo "dry-run, would run: ${CMD[*]}"
    echo "on the phone: $0 --apply"
    exit 0 ;;
  --apply) ;;
  *) echo "usage: $0 [--apply]" >&2; exit 2 ;;
esac

if claude mcp get cliphist >/dev/null 2>&1; then echo "cliphist: already registered, nothing to do"; exit 0; fi
[ -s "$TOKEN_FILE" ] || { echo "register-cliphist-mcp: no token in $TOKEN_FILE" >&2; exit 2; }
[ -f "$SERVER" ] || { echo "register-cliphist-mcp: $SERVER not built (cd mcp-server && npm ci && npm run build)" >&2; exit 2; }
echo "running: ${CMD[*]}"
"${CMD[@]}"
