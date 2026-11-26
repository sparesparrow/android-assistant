#!/bin/bash
# Speak text with Android's own TTS engine (Google, Settings > Text-to-speech output): no API key,
# works offline with downloaded voices. Needs the Tasker task "Claude Say"
# (phone/tasker/build-claude-say.py) and Tasker's "Allow External Access".
#
#   android-say.sh [--wait] "text"      or      echo text | android-say.sh [--wait]
#
# --wait returns only once Tasker has finished speaking: it watches Android's audio focus stack
# for Tasker (it takes focus while the Say action runs). Exit 3 if Tasker never started speaking
# within SAY_START_TIMEOUT seconds (default 6): task missing, external access off, Tasker stopped.
#
# The text goes through a file (/sdcard/assistant/say.txt), not an intent extra: am's list extras
# split on commas, and the file keeps any punctuation and diacritics intact.
set -euo pipefail
SERIAL=${DROID_SERIAL:-127.0.0.1:5555}
FILE=/sdcard/assistant/say.txt
WAIT=0; [ "${1:-}" = --wait ] && { WAIT=1; shift; }
text=${1:-$(cat)}
[ -n "${text//[[:space:]]/}" ] || { echo "android-say: nothing to say" >&2; exit 2; }
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
printf '%s' "$text" > "$tmp"
adb -s "$SERIAL" shell mkdir -p /sdcard/assistant
adb -s "$SERIAL" push "$tmp" "$FILE" >/dev/null
adb -s "$SERIAL" shell am broadcast -a net.dinglisch.android.tasker.ACTION_TASK --es task_name "'Claude Say'" >/dev/null
focused() { adb -s "$SERIAL" shell dumpsys audio 2>/dev/null \
  | sed -n '/Audio Focus stack entries/,/^$/p' | grep -q net.dinglisch.android.tasker; }
if [ "$WAIT" = 1 ]; then
  end=$(( SECONDS + ${SAY_START_TIMEOUT:-6} ))
  until focused; do
    [ $SECONDS -lt $end ] || { echo "android-say: Tasker did not start speaking (is the task 'Claude Say' imported and external access on?)" >&2; exit 3; }
  done
  end=$(( SECONDS + 20 + ${#text} / 8 ))   # generous: Google TTS speaks ~15 characters/s
  while focused; do
    [ $SECONDS -lt $end ] || { echo "android-say: still speaking after the time limit, not waiting longer" >&2; exit 4; }
  done
  echo "android-say: spoke ${#text} characters via Tasker's Claude Say" >&2
else
  echo "android-say: sent ${#text} characters to Tasker's Claude Say"
fi
