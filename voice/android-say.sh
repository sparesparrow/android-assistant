#!/bin/bash
# Speak text with Android's own TTS engine (Google, Settings > Text-to-speech output): no API key,
# works offline with downloaded voices. Needs the Tasker task "Claude Say"
# (phone/tasker/build-claude-say.py) and Tasker's "Allow External Access".
#
#   android-say.sh "text"      or      echo text | android-say.sh
#
# The text goes through a file (/sdcard/assistant/say.txt), not an intent extra: am's list extras
# split on commas, and the file keeps any punctuation and diacritics intact.
set -euo pipefail
SERIAL=${DROID_SERIAL:-127.0.0.1:5555}
FILE=/sdcard/assistant/say.txt
text=${1:-$(cat)}
[ -n "${text//[[:space:]]/}" ] || { echo "android-say: nothing to say" >&2; exit 2; }
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
printf '%s' "$text" > "$tmp"
adb -s "$SERIAL" shell mkdir -p /sdcard/assistant
adb -s "$SERIAL" push "$tmp" "$FILE" >/dev/null
adb -s "$SERIAL" shell am broadcast -a net.dinglisch.android.tasker.ACTION_TASK --es task_name "'Claude Say'" >/dev/null
echo "android-say: sent ${#text} characters to Tasker's Claude Say"
