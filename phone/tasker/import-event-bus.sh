#!/bin/bash
# Import the Tasker project "Event Bus" (build-event-bus.py) and install its Termux targets:
#   ~/.termux/tasker/event-<kind>   Tasker RUN_COMMAND target, event JSON on stdin ->
#     proot-distro login debian -> /root/android-assistant/phone/tasker/event-publish.sh <kind>
#     -> mosquitto_pub phone/events/<kind>          (docs/event-bus.md)
# Needs: mosquitto-clients and python3 in this PRoot, allow-external-apps = true in Termux
# (assistant/agents/install.sh sets it). Extra args go to build-event-bus.py, e.g.
#   import-event-bus.sh --car-bt "My Car" --allow com.example.app
# The AutoNotification profile is made in Tasker's UI afterwards (printed at the end).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=tasker-import.sh
. "$HERE/tasker-import.sh"
TH=/data/data/com.termux/files/home
KINDS="battery bluetooth notification"   # = KINDS in build-event-bus.py
PUBLISHER=/root/android-assistant/phone/tasker/event-publish.sh
PUB_CONF=/root/phone/state/mqtt-publisher   # XDG_CONFIG_HOME of the publisher (its mosquitto_pub options file)

command -v mosquitto_pub >/dev/null || { echo "mosquitto_pub missing in the PRoot: apt install mosquitto-clients" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 missing in the PRoot" >&2; exit 1; }
[ -x "$PUBLISHER" ] || { echo "not executable: $PUBLISHER" >&2; exit 1; }
grep -qE '^[[:space:]]*allow-external-apps[[:space:]]*=[[:space:]]*true' "$TH/.termux/termux.properties" 2>/dev/null ||
  echo "warning: Termux allow-external-apps is not true; run assistant/agents/install.sh first" >&2

mkdir -p "$TH/.termux/tasker"
for k in $KINDS; do
  printf '#!/data/data/com.termux/files/usr/bin/sh\n# Tasker RUN_COMMAND target: one event JSON on stdin -> MQTT phone/events/%s (docs/event-bus.md).\nexec proot-distro login debian -- /usr/bin/env XDG_CONFIG_HOME=%s %s %s 2>>"$HOME/.termux/tasker/event-publish.log"\n' \
    "$k" "$PUB_CONF" "$PUBLISHER" "$k" >"$TH/.termux/tasker/event-$k"
  chmod 755 "$TH/.termux/tasker/event-$k"
done
echo "termux: ~/.termux/tasker/event-{$(echo $KINDS | tr ' ' ,)}"

tmpd=$(mktemp -d)
trap 'rm -rf "$tmpd"; _tasker_adb shell "rm -f ${TASKER_IMPORT_DEST:-/nonexistent}" >/dev/null 2>&1 || true' EXIT
python3 -I "$HERE/build-event-bus.py" "$tmpd/EventBus.prj.xml" "$@"
tasker_import "$tmpd/EventBus.prj.xml" EventBus.prj.xml
cat <<'TXT'
Next, in Tasker's UI (verify on the phone): Profile -> Event -> Plugin -> AutoNotification ->
Intercept, Action Type Created, Apps: Gemini (com.google.android.apps.bard); task EB Notification.
Test without Tasker: printf '%s' '{"kind":"battery","source_pkg":"android","title":"t","text":"","when":0,"id":"x"}' |
  phone-services native '~/.termux/tasker/event-battery'   (while: mosquitto_sub -t 'phone/events/#' -v)
TXT
