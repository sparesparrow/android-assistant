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
#   Both only steer the checks here; the registered server uses the wrapper defaults (warned).
#
# The server also exposes add_to_clipboard: keep it out of allowedTools and deny it. Clipboard
# item content is untrusted data.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WRAPPER=$ROOT/voice/cliphist-mcp.sh
TOKEN_FILE=${CLIPHIST_TOKEN_FILE:-$ROOT/voice/state/cliphist-token}
SERVER=${CLIPHIST_MCP:-/root/cliphist-android/mcp-server/dist/index.js}
CMD=(claude mcp add --scope user cliphist -- "$WRAPPER")

case ${1:-} in ""|--apply) ;; *) echo "usage: $0 [--apply]" >&2; exit 2 ;; esac
if [ -n "${CLIPHIST_TOKEN_FILE:-}${CLIPHIST_MCP:-}" ]; then
  echo "warning: CLIPHIST_TOKEN_FILE/CLIPHIST_MCP are not part of the registration; the registered server will use the wrapper defaults" >&2
fi

case ${1:-} in
  "")
    if [ -s "$TOKEN_FILE" ]; then echo "token: $TOKEN_FILE (non-empty)"; else echo "token: none in $TOKEN_FILE (--apply refuses)"; fi
    if [ -f "$SERVER" ]; then echo "server: $SERVER"; else echo "server: $SERVER not built (--apply refuses)"; fi
    echo "dry-run, would run: ${CMD[*]}"
    echo "on the phone: $0 --apply"
    exit 0 ;;
esac

# Skip only a user-scope entry whose command is exactly the wrapper; any other `cliphist` entry
# (e.g. node with the token in its env, or a local/project one shadowing the user entry) is left
# for the owner. Its output is never printed, it may hold env values.
if existing=$(claude mcp get cliphist 2>/dev/null); then
  user=no; grep -q '^  Scope: User config' <<<"$existing" && user=yes
  if [ "$user" = yes ] && grep -qxF -- "  Command: $WRAPPER" <<<"$existing"; then
    echo "cliphist: already registered, nothing to do"; exit 0
  fi
  if [ "$user" = no ]; then
    echo "register-cliphist-mcp: the effective 'cliphist' entry is not user scope (a local or project" \
      "entry shadows the user one); inspect it with 'claude mcp get cliphist' and remove it with" \
      "'claude mcp remove cliphist -s <scope>' (local or project), then rerun" >&2
  else
    echo "register-cliphist-mcp: a different 'cliphist' entry exists (not $WRAPPER); inspect it with" \
      "'claude mcp get cliphist' and remove it with 'claude mcp remove cliphist -s <scope>', then rerun" >&2
  fi
  exit 2
fi
[ -s "$TOKEN_FILE" ] || { echo "register-cliphist-mcp: no token in $TOKEN_FILE" >&2; exit 2; }
[ -f "$SERVER" ] || { echo "register-cliphist-mcp: $SERVER not built (cd mcp-server && npm ci && npm run build)" >&2; exit 2; }
echo "running: ${CMD[*]}"
"${CMD[@]}"
