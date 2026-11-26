#!/usr/bin/env bash
# Publish ONE phone event to the local MQTT event bus (docs/event-bus.md, mcp-prompts#140).
#
#   event-publish.sh <kind> < event.json
#
# stdin: one JSON object with the keys kind, source_pkg, title, text, when, id ("kind" equal to
# <kind>). It is checked, compacted to a single line (UTF-8, diacritics kept) and published,
# not retained, QoS 0, to phone/events/<kind> on ${MQTT_HOST:-127.0.0.1} (default port 1883):
#   mosquitto_pub -h HOST -t phone/events/<kind> -s      (payload on stdin, never in argv)
# A bad kind, invalid JSON or a missing key: a message on stderr, a non-zero exit and nothing
# published. Error messages never repeat the event text.
#
# Where it runs: inside the Debian PRoot, where mosquitto_pub and python3 are. Tasker reaches it
# through Termux RUN_COMMAND -> ~/.termux/tasker/event-<kind> (installed by import-event-bus.sh)
# -> proot-distro login debian -> this script, the event JSON on stdin the whole way.
# Broker credentials (only once the ACL in phone/mqtt/ is installed) come from mosquitto_pub's
# own options file, $XDG_CONFIG_HOME/mosquitto_pub; the wrappers point XDG_CONFIG_HOME at
# /root/phone/state/mqtt-publisher. No password is ever on a command line.
set -euo pipefail
export LC_ALL=C   # [a-z] below means ASCII a-z, whatever the caller's locale

usage() { echo "usage: event-publish.sh <kind> < event.json   (kind: 1-32 of a-z 0-9 _ -)" >&2; exit 2; }
[ "$#" -eq 1 ] || usage
kind=$1
if ! [[ $kind =~ ^[a-z0-9_-]{1,32}$ ]]; then
  echo "event-publish: kind not allowed: use 1-32 characters a-z 0-9 _ - (no MQTT wildcards or /)" >&2
  exit 2
fi

# Check and compact in Python (isolated mode: no user site, no cwd on sys.path). The kind is an
# argv element, never part of the code. The result is captured first, so a refused event never
# starts mosquitto_pub.
PY=$(cat <<'PYEOF'
import json, re, sys
KEYS = ("kind", "source_pkg", "title", "text", "when", "id")
MAX = 65536
def fail(msg):
    sys.stderr.write("event-publish: " + msg + "; nothing published\n")
    sys.exit(65)
def no_constant(name):
    raise ValueError("NaN/Infinity are not JSON")
def no_duplicates(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise ValueError("duplicate key")
        obj[k] = v
    return obj
kind = sys.argv[1]
if not re.fullmatch(r"[a-z0-9_-]{1,32}", kind):
    fail("kind not allowed")
raw = sys.stdin.buffer.read(MAX + 1)
if len(raw) > MAX:
    fail("event larger than %d bytes" % MAX)
try:
    ev = json.loads(raw.decode("utf-8"), parse_constant=no_constant, object_pairs_hook=no_duplicates)
except UnicodeDecodeError:
    fail("stdin is not UTF-8")
except ValueError as e:
    fail("stdin is not one valid JSON value (%s)" % type(e).__name__)
if not isinstance(ev, dict):
    fail("the event must be a JSON object")
missing = [k for k in KEYS if k not in ev]
if missing:
    fail("missing key(s): " + ", ".join(missing))
if ev["kind"] != kind:
    fail("the event's \"kind\" does not match <kind>")
out = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
# json escapes control characters, but not these, and wait_event splits lines with
# str.splitlines(), which breaks on them. They can only sit inside strings: escape them.
for ch in ("\u0085", " ", " "):
    out = out.replace(ch, "\\u%04x" % ord(ch))
try:
    data = out.encode("utf-8")
except UnicodeEncodeError:
    fail("the event holds an unpaired surrogate")
sys.stdout.buffer.write(data)
PYEOF
)
payload=$(python3 -I -c "$PY" "$kind")

command -v mosquitto_pub >/dev/null 2>&1 || { echo "event-publish: mosquitto_pub is not installed (apt install mosquitto-clients); nothing published" >&2; exit 127; }
printf '%s' "$payload" | mosquitto_pub -h "${MQTT_HOST:-127.0.0.1}" -t "phone/events/$kind" -s
