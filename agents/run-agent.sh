#!/bin/bash
# Run a Claude Code agent from ~/.claude/agents headless, prompt on stdin, and post the result
# as a phone notification. Entry point for Tasker (via termux/claude-agent) and for the shell.
#
#   echo "question +Deep Research" | run-agent.sh gemini [tasker]
#   run-agent.sh --ssh        forced command of the PRoot sshd: agent from $SSH_ORIGINAL_COMMAND
#
# Phone-driving agents share one screen, so runs are serialized with a lock.
set -uo pipefail
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
AGENTS_DIR=${AGENTS_DIR:-$HERE}
STATE=${ASSISTANT_STATE:-/root/phone/state}
LOG=${AGENT_LOG:-/root/phone/logs/agents.log}
PROMPT_MAX=8000
export PATH="/root/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH" HOME=/root

if [ "${1:-}" = --ssh ]; then
  read -r agent origin _ <<<"${SSH_ORIGINAL_COMMAND:-}"
else
  agent=${1:-}; origin=${2:-shell}
fi
case $agent in ''|*[!a-z0-9-]*) echo "usage: run-agent.sh <agent> [origin] < prompt" >&2; exit 2 ;; esac
[ -f "$AGENTS_DIR/$agent.md" ] || { echo "no agent '$agent' in $AGENTS_DIR" >&2; exit 2; }
case $origin in tasker|shell|ssh) ;; *) origin=ssh ;; esac

prompt=$(head -c $((PROMPT_MAX + 1)) | tr -d '\r')
[ -n "${prompt//[[:space:]]/}" ] || { echo "empty prompt" >&2; exit 2; }
[ ${#prompt} -le $PROMPT_MAX ] || { echo "prompt longer than $PROMPT_MAX characters" >&2; exit 2; }
if [ "$origin" = tasker ]; then
  prompt+=$'\n\n(Started from a Tasker task on the phone. Nobody can answer questions during this run; finish on your own and end with the report.)'
fi

mkdir -p "$STATE" "$(dirname "$LOG")"
log() { printf '%s [%s/%s] %s\n' "$(date '+%F %T')" "$agent" "$origin" "$*" >>"$LOG"; }
notify() {  # title, text: phone notification over the adb loopback (as the MCP notify tool)
  local q t
  q=$(printf '%s' "${2:0:1000}" | sed "s/'/'\\\\''/g"); t=$(printf '%s' "${1:0:80}" | sed "s/'/'\\\\''/g")
  timeout 15 adb -s 127.0.0.1:5555 shell "cmd notification post -S bigtext -t '$t' claude-agent-$agent '$q'" >/dev/null 2>&1
}

command -v phone-services >/dev/null 2>&1 && phone-services ensure >/dev/null 2>&1
[ -x /root/phone/phone-services ] && /root/phone/phone-services ensure >/dev/null 2>&1

exec 9>"$STATE/agent.lock"
flock -w 600 9 || { log "busy: another agent kept the phone for 10 min"; notify "Claude $agent" "busy: another agent is using the phone"; exit 75; }
# child agents must not take the lock again (they would wait on this runner)
export PHONE_SCREEN_LOCK=held
log "start (${#prompt} chars)"
start=$(date +%s)
out=$(cd /root && timeout 1800 claude --agent "$agent" -p "$prompt" </dev/null 2>>"$LOG"); rc=$?
log "end rc=$rc $(( $(date +%s) - start ))s"
[ $rc -eq 0 ] && [ -n "$out" ] || out="agent $agent failed (rc=$rc); see $LOG"
[ "$origin" = shell ] || notify "Claude $agent" "$out"
printf '%s\n' "$out"
exit $rc
