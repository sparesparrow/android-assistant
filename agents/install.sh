#!/bin/bash
# Install the phone agents (idempotent):
#   1. ~/.claude/agents/<name>.md -> symlinks to assistant/agents/*.md  (claude --agent <name>)
#   2. Termux ~/.termux/tasker/claude-agent + agent-<name> wrappers     (Tasker RUN_COMMAND targets)
#   3. a dedicated Termux key, authorized in the PRoot agent sshd only (forced command run-agent.sh)
#   4. allow-external-apps = true in ~/.termux/termux.properties         (lets Tasker's RUN_COMMAND in)
#   5. phone-services ensure -> agent sshd on 127.0.0.1:8023
# Then import the Tasker tasks: /root/phone/tasker/import-claude-agents.sh (tap YES in Tasker).
set -euo pipefail
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
PS=/root/phone/phone-services
TH=/data/data/com.termux/files/home
STATE=/root/phone/state
names=$(cd "$HERE" && ls *.md | sed 's/\.md$//')

mkdir -p /root/.claude/agents
for n in $names; do ln -sfn "$HERE/$n.md" "/root/.claude/agents/$n.md"; done
echo "agents: $(echo $names) -> ~/.claude/agents"

mkdir -p "$TH/.termux/tasker"
install -m 755 "$HERE/termux/claude-agent" "$TH/.termux/tasker/claude-agent"
for n in $names; do
  printf '#!/data/data/com.termux/files/usr/bin/sh\n# Tasker RUN_COMMAND target: prompt on stdin.\nexec "$HOME/.termux/tasker/claude-agent" %s tasker\n' "$n" \
    >"$TH/.termux/tasker/agent-$n"
  chmod 755 "$TH/.termux/tasker/agent-$n"
done
echo "termux: ~/.termux/tasker/claude-agent, agent-{$(echo $names | tr ' ' ,)}"

$PS native 'test -f ~/.ssh/proot_agent_ed25519 || ssh-keygen -q -t ed25519 -N "" -C tasker-proot-agent -f ~/.ssh/proot_agent_ed25519'
pub=$($PS native 'cat ~/.ssh/proot_agent_ed25519.pub')
case $pub in ssh-ed25519\ *) ;; *) echo "could not read the Termux agent key" >&2; exit 1 ;; esac
mkdir -p "$STATE"; umask 077
printf 'restrict,command="%s --ssh" %s\n' "$HERE/run-agent.sh" "$pub" >"$STATE/agent_authorized_keys"
echo "key: Termux ~/.ssh/proot_agent_ed25519 authorized for the agent sshd only"

$PS native 'f=~/.termux/termux.properties; touch $f
  if grep -qE "^[[:space:]]*allow-external-apps[[:space:]]*=[[:space:]]*true" $f; then :;
  elif grep -qE "^#[[:space:]]*allow-external-apps" $f; then sed -i -E "s/^#[[:space:]]*allow-external-apps.*/allow-external-apps = true/" $f;
  else echo "allow-external-apps = true" >>$f; fi; termux-reload-settings'
echo "termux: allow-external-apps = true"

$PS ensure
$PS status | grep -E '^agents'
echo "next: /root/phone/tasker/import-claude-agents.sh   (Tasker shows Import Data; tap YES)"
