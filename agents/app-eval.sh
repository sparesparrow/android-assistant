#!/bin/sh
# Live eval of the app agents (mcp-prompts #142): one short real task per chat app, scored, with
# app versions. Runs under the one-screen lock. See mcp/app_eval.py for what is measured.
#
#   app-eval.sh run [APP ...]   the suite, all apps by default; exit 1 if any app failed
#   app-eval.sh check           installed versions against the last evaluated ones
#   app-eval.sh updated PKG     run the app with this package name
#   app-eval.sh watch           every APP_EVAL_INTERVAL s (default 1800), run the apps whose
#                               version changed, once the phone is idle (awake, unlocked, on the
#                               home screen); one watcher at a time. phone-services starts it when
#                               $ASSISTANT_STATE/app-eval/watch.enabled exists.
#
# Results: $ASSISTANT_STATE/app-eval/latest.md (and <run>/results.{md,json}, history.jsonl).
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
STATE=${ASSISTANT_STATE:-/root/phone/state}
PY=${APP_EVAL_PY:-$HERE/mcp/.venv/bin/python}
[ -x "$PY" ] || PY=python3
EVAL="$PY $HERE/mcp/app_eval.py"
mkdir -p "$STATE/app-eval"

# The suite drives the screen: take agent.lock unless run-agent.sh already holds it for us.
locked() {
  if [ "${PHONE_SCREEN_LOCK:-}" = held ]; then
    "$@"
  else
    rc=0
    flock -E 75 -w "${APP_EVAL_LOCK_WAIT:-600}" "$STATE/agent.lock" env PHONE_SCREEN_LOCK=held "$@" || rc=$?
    [ $rc -eq 75 ] && echo "app-eval: the screen is busy (agent.lock); try again later" >&2
    return $rc
  fi
}

case "${1:-}" in
run) shift; locked $EVAL run "$@" ;;
updated) shift; locked $EVAL updated "$@" ;;
check) $EVAL check ;;
watch)
  exec 8>"$STATE/app-eval/watch.lock"
  flock -n 8 || { echo "app-eval: a watcher already runs" >&2; exit 0; }
  while :; do
    apps=$($EVAL changed --when-idle 2>>"$STATE/app-eval/watch.log" || true)
    if [ -n "$apps" ]; then
      echo "$(date '+%F %T') changed: $apps" >>"$STATE/app-eval/watch.log"
      # shellcheck disable=SC2086 # one word per app
      locked $EVAL run $apps >>"$STATE/app-eval/watch.log" 2>&1 || true
    fi
    sleep "${APP_EVAL_INTERVAL:-1800}"
  done ;;
*) sed -n '2,12p' "$0" >&2; exit 2 ;;
esac
