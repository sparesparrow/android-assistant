#!/bin/bash
# Import the Tasker project "Claude Agents" (a task "Agent <name>" per assistant/agents/*.md).
# Run assistant/agents/install.sh first: it installs the Termux scripts the tasks call.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=tasker-import.sh
. "$HERE/tasker-import.sh"
tmpd=$(mktemp -d)
trap 'rm -rf "$tmpd"; _tasker_adb shell "rm -f ${TASKER_IMPORT_DEST:-/nonexistent}" >/dev/null 2>&1 || true' EXIT
python3 -I "$HERE/build-claude-agents.py" "$tmpd/ClaudeAgents.prj.xml"
tasker_import "$tmpd/ClaudeAgents.prj.xml" ClaudeAgents.prj.xml
echo "Run a task from Tasker, a Quick Settings tile or a widget; results arrive as notifications."
