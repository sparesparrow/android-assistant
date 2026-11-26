#!/usr/bin/env python3
"""Build the Tasker project "Event Bus": phone events -> JSON -> MQTT phone/events/<kind>, as one
.prj.xml for Tasker's own importer (import-event-bus.sh). docs/event-bus.md, mcp-prompts#140.

  build-event-bus.py OUT.prj.xml [--allow PKG ...] [--car-bt NAME]   real build
  build-event-bus.py --dry-run [--allow PKG ...] [--car-bt NAME]     XML to stdout

Profiles (Tasker states; enter task, and exit task where the state ending is an event too):
  EB Battery Low    battery level 0..15 %        -> EB Battery Low       (kind battery)
  EB Charging       power, any source            -> EB Charging / EB Not Charging   (battery)
  EB Car Bluetooth  BT connected (--car-bt NAME;  -> EB Car BT Connected / EB Car BT Disconnected
                    empty = any device)             (kind bluetooth)
Task EB Notification (kind notification) is for an AutoNotification Intercept profile that the
owner creates in Tasker's UI (see NOTIFICATION_PROFILE_NOTE): this builder writes no plugin XML.

Every task is one JavaScriptlet: it builds the event {kind, source_pkg, title, text, when, id}
with JSON.stringify and hands it to Termux's RunCommandService:
  com.termux.RUN_COMMAND_PATH       ~/.termux/tasker/event-<kind>  (a constant; import-event-bus.sh
                                    installs it: proot-distro login debian -> event-publish.sh)
  com.termux.RUN_COMMAND_STDIN      the event JSON (the only place event data goes)
  com.termux.RUN_COMMAND_BACKGROUND true
No RUN_COMMAND_ARGUMENTS, no shell() and no event text on any command line. EB Notification
publishes only notifications whose package is in the allowlist (ALLOW, plus --allow), checked
in the JavaScriptlet itself, whatever the profile's own filter says. No token or secret is in
this XML.

Formats follow Tasker 6.7.6 as in build-claude-panel.py (action 129 = JavaScriptlet). The
State codes and arguments below (STATE_*) are NOT verified against an export from this phone:
verify on the phone (Tasker shows the profile's contexts on import; check them before YES).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("panel", HERE / "build-claude-panel.py")
panel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(panel)
sub, s_arg, i_arg, javascriptlet, to_xml = panel.sub, panel.s_arg, panel.i_arg, panel.javascriptlet, panel.to_xml

PROJECT = "Event Bus"
TERMUX_TASKER = "/data/data/com.termux/files/home/.termux/tasker"
KINDS = ("battery", "bluetooth", "notification")   # one Termux wrapper event-<kind> each
ALLOW = ("com.google.android.apps.bard",)          # Gemini app: "Deep Research is ready"
PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
BATTERY_LOW_MAX = 15
TIME = "1794800000000"
FIRST_TASK_ID = 9401
FIRST_PROFILE_ID = 9451
PROFILE_FLAGS = "10"   # as the verified Assistance Request profile in build-claude-panel.py

# Verify on the phone: Tasker State codes as remembered from Tasker exports, not checked here.
STATE_BATTERY_LEVEL = 170   # arg0 from %, arg1 to %
STATE_POWER = 10            # arg0 source: 0 any, 1 AC, 2 USB, 3 wireless
STATE_BT_CONNECTED = 3      # arg0 device name (empty = any), arg1 address

NOTIFICATION_PROFILE_NOTE = (
    "Create in Tasker's UI (verify on the phone): Profile -> Event -> Plugin -> AutoNotification "
    "-> Intercept; Action Type Created; Apps: the allowlisted packages; task: EB Notification. "
    "AutoNotification's plugin XML (bundle, event code) is not known here, so it is not written. "
    "Variables read (verify on the phone): %anpackage, %antitle, %antext, %ankey.")

# ----------------------------------------------------------------------------- JavaScript
JS_LIB = r"""
var EB_MAX = {title: 500, text: 2000, other: 200};
function ebCut(v, max) { return String(v === undefined || v === null ? "" : v).substring(0, max); }
function ebEvent(kind, pkg, title, text, id) {
  var when = Date.now();
  return {kind: kind, source_pkg: ebCut(pkg, EB_MAX.other), title: ebCut(title, EB_MAX.title),
          text: ebCut(text, EB_MAX.text), when: when, id: ebCut(id || (kind + "|" + pkg + "|" + when), EB_MAX.other)};
}
"""


def tasker_var(name: str) -> str:
    """JS expression for Tasker local %name: '' when unset or left unresolved as '%name'."""
    return (f"((typeof {name} !== 'undefined' && String({name}) !== {json.dumps('%' + name)})"
            f" ? String({name}) : '')")


def js_send(kind: str) -> str:
    """The only way out of a task: the event JSON in RUN_COMMAND_STDIN, a constant PATH."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    path = f"{TERMUX_TASKER}/event-{kind}"
    return (
        "function ebSend(ev) {\n"
        "  sendIntent('com.termux.RUN_COMMAND', 'service', 'com.termux',\n"
        "    'com.termux.app.RunCommandService', 'none', '', '',\n"
        f"    [{json.dumps('com.termux.RUN_COMMAND_PATH:' + path)},\n"
        "     'com.termux.RUN_COMMAND_STDIN:' + JSON.stringify(ev),\n"
        "     'com.termux.RUN_COMMAND_BACKGROUND:true']);\n"
        "}\n")


def js_battery(title: str, with_level: bool) -> str:
    level = ("var lvl = ''; try { lvl = String(global('BATT') || ''); } catch (e) {}\n"
             "if (lvl.charAt(0) == '%') lvl = '';\n") if with_level else "var lvl = '';\n"
    return level + (f"ebSend(ebEvent('battery', 'android', {json.dumps(title)}, "
                    "lvl ? lvl + ' %' : ''));\n")


def js_bluetooth(title: str, device: str) -> str:
    return f"ebSend(ebEvent('bluetooth', 'android', {json.dumps(title)}, {json.dumps(device)}));\n"


def js_notification(allow: list[str]) -> str:
    return (
        f"var EB_ALLOW = {json.dumps(allow)};\n"
        f"var pkg = {tasker_var('anpackage')};\n"
        "if (EB_ALLOW.indexOf(pkg) >= 0) {\n"
        f"  ebSend(ebEvent('notification', pkg, {tasker_var('antitle')}, {tasker_var('antext')}, "
        f"{tasker_var('ankey')}));\n"
        "}\n")


def task_js(kind: str, body: str) -> str:
    return JS_LIB.strip() + "\n" + js_send(kind) + body


# ----------------------------------------------------------------------------- XML
def state(prof, code: int, args: list[tuple[str, object]]):
    st = sub(prof, "State", sr="con0", ve="2")
    sub(st, "code", str(code))
    for n, (typ, val) in enumerate(args):
        if typ == "int":
            i_arg(st, n, val)
        else:
            s_arg(st, n, val or None)
    return st


def build(allow: list[str] | None = None, car_bt: str = "") -> ET.Element:
    allow = list(dict.fromkeys([*ALLOW, *(allow or [])]))
    bad = [p for p in allow if not PKG_RE.match(p)]
    if bad:
        raise ValueError(f"not an Android package name: {bad}")
    if len(car_bt) > 64 or any(ord(c) < 32 for c in car_bt):
        raise ValueError("Bluetooth device name: at most 64 printable characters")

    tasks = [  # name, kind, JavaScriptlet body
        ("EB Battery Low", "battery", js_battery("Battery low", True)),
        ("EB Charging", "battery", js_battery("Charging", True)),
        ("EB Not Charging", "battery", js_battery("Not charging", True)),
        ("EB Car BT Connected", "bluetooth", js_bluetooth("Bluetooth connected", car_bt)),
        ("EB Car BT Disconnected", "bluetooth", js_bluetooth("Bluetooth disconnected", car_bt)),
        ("EB Notification", "notification", js_notification(allow)),
    ]
    tid = {name: FIRST_TASK_ID + i for i, (name, _, _) in enumerate(tasks)}
    profiles = [  # name, enter task, exit task, state code, state args
        ("EB Battery Low", "EB Battery Low", None, STATE_BATTERY_LEVEL,
         [("int", 0), ("int", BATTERY_LOW_MAX)]),
        ("EB Charging", "EB Charging", "EB Not Charging", STATE_POWER, [("int", 0)]),
        ("EB Car Bluetooth", "EB Car BT Connected", "EB Car BT Disconnected", STATE_BT_CONNECTED,
         [("str", car_bt), ("str", "")]),
    ]
    pid = {name: FIRST_PROFILE_ID + i for i, (name, *_) in enumerate(profiles)}

    root = ET.Element("TaskerData", sr="", dvi="1", tv="6.7.6-beta")
    # No XML comment for NOTIFICATION_PROFILE_NOTE: nothing shows Tasker's importer takes them.
    proj = sub(root, "Project", sr="proj0", ve="2")
    sub(proj, "cdate", TIME)
    sub(proj, "id", str(uuid.uuid5(uuid.NAMESPACE_URL, "event-bus-tasker-project")))
    sub(proj, "name", PROJECT)
    sub(proj, "pids", ",".join(str(i) for i in pid.values()))
    sub(proj, "tids", ",".join(str(i) for i in tid.values()))

    for name, enter, leave, code, args in profiles:
        prof = sub(root, "Profile", sr=f"prof{pid[name]}", ve="2")
        fields = [("cdate", TIME), ("edate", TIME), ("flags", PROFILE_FLAGS), ("id", pid[name]),
                  ("mid0", tid[enter])]
        if leave:
            fields.append(("mid1", tid[leave]))
        fields.append(("nme", name))
        for tag, val in fields:
            sub(prof, tag, val)
        state(prof, code, args)

    for name, kind, body in tasks:
        t = sub(root, "Task", sr=f"task{tid[name]}")
        for tag, val in (("cdate", TIME), ("edate", TIME), ("id", tid[name]), ("nme", name),
                         ("pri", "100")):
            sub(t, tag, val)
        javascriptlet(t, 0, task_js(kind, body), 10)
    return root


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", nargs="?")
    ap.add_argument("--dry-run", action="store_true", help="print the XML")
    ap.add_argument("--allow", action="append", default=[], metavar="PKG",
                    help=f"also publish notifications of PKG (always: {', '.join(ALLOW)})")
    ap.add_argument("--car-bt", default="", metavar="NAME",
                    help="Bluetooth device name of the car (default: any device)")
    a = ap.parse_args(argv)
    try:
        xml = to_xml(build(a.allow, a.car_bt))
    except ValueError as e:
        ap.error(str(e))
    if a.dry_run:
        sys.stdout.write(xml)
        return 0
    if not a.out or not a.out.endswith(".prj.xml"):
        ap.error("need OUT ending in .prj.xml (Tasker's importer goes by the extension)")
    Path(a.out).write_text(xml)
    print(f"wrote {a.out}: profiles EB Battery Low, EB Charging, EB Car Bluetooth; "
          f"task EB Notification (attach it to an AutoNotification Intercept profile in Tasker)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
