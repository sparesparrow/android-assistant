"""Phone side of the event bus (docs/event-bus.md, mcp-prompts#140): the Tasker project builder
phone/tasker/build-event-bus.py and the publisher phone/tasker/event-publish.sh, run against a
stub mosquitto_pub (argv and stdin logged). No phone, no broker."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

TASKER = Path(__file__).resolve().parents[1] / "phone" / "tasker"
MQTT = Path(__file__).resolve().parents[1] / "phone" / "mqtt"
BUILDER = TASKER / "build-event-bus.py"
PUBLISH = TASKER / "event-publish.sh"
IMPORT = TASKER / "import-event-bus.sh"
spec = importlib.util.spec_from_file_location("build_event_bus", BUILDER)
eb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eb)
node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
GEMINI = "com.google.android.apps.bard"
EVIL = "ř'\"`touch evil1` $(touch evil2); | & \n %antext"
EVENT = {"kind": "notification", "source_pkg": GEMINI, "title": "Gemini",
         "text": "Výzkum je hotový", "when": 1764150000000, "id": "0|com.google...|42"}


# ----------------------------------------------------------------------------- builder
def dry_run(*args):
    p = subprocess.run(["python3", "-I", str(BUILDER), "--dry-run", *args],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


def scripts(r):
    return {t.findtext("nme"): [next(x.text for x in a.findall("Str") if x.get("sr") == "arg0")
                                for a in t.iter("Action") if a.findtext("code") == "129"]
            for t in r.iter("Task")}


def test_dry_run_is_xml_with_the_profiles_and_tasks():
    r = ET.fromstring(dry_run())
    assert r.tag == "TaskerData"
    p = r.find("Project")
    assert p.findtext("name") == "Event Bus"
    tids = {t.findtext("id"): t.findtext("nme") for t in r.iter("Task")}
    pids = {pr.findtext("id"): pr for pr in r.iter("Profile")}
    assert set(p.findtext("tids").split(",")) == set(tids)
    assert set(p.findtext("pids").split(",")) == set(pids)
    got = {pr.findtext("nme"): (tids[pr.findtext("mid0")], tids.get(pr.findtext("mid1")),
                                pr.find("State").findtext("code"))
           for pr in pids.values()}
    assert got == {
        "EB Battery Low": ("EB Battery Low", None, str(eb.STATE_BATTERY_LEVEL)),
        "EB Charging": ("EB Charging", "EB Not Charging", str(eb.STATE_POWER)),
        "EB Car Bluetooth": ("EB Car BT Connected", "EB Car BT Disconnected", str(eb.STATE_BT_CONNECTED)),
    }
    assert "EB Notification" in tids.values()
    # every task is a single JavaScriptlet
    for t in r.iter("Task"):
        assert [a.findtext("code") for a in t.iter("Action")] == ["129"]


def test_battery_low_range_and_car_name():
    r = ET.fromstring(dry_run("--car-bt", "Škoda Octavia"))
    prof = {p.findtext("nme"): p for p in r.iter("Profile")}
    assert [i.get("val") for i in prof["EB Battery Low"].find("State").findall("Int")] == ["0", "15"]
    assert prof["EB Car Bluetooth"].find("State").find("Str").text == "Škoda Octavia"
    assert json.dumps("Škoda Octavia") in scripts(r)["EB Car BT Connected"][0]


SEND_RE = re.compile(r"sendIntent\('com\.termux\.RUN_COMMAND', 'service', 'com\.termux',\s*"
                     r"'com\.termux\.app\.RunCommandService', 'none', '', '',\s*\[(.*?)\]\);", re.S)


def test_every_run_command_has_a_constant_path_and_the_event_only_in_stdin():
    kinds = set()
    for name, js in scripts(ET.fromstring(dry_run("--allow", "org.example.app"))).items():
        js = js[0]
        calls = SEND_RE.findall(js)
        assert len(calls) == 1 and js.count("sendIntent(") == 1, name
        extras = [e.strip() for e in calls[0].split(",\n")]
        m = re.fullmatch(r'"com\.termux\.RUN_COMMAND_PATH:' + re.escape(eb.TERMUX_TASKER) +
                         r'/event-([a-z0-9_-]{1,32})"', extras[0])
        assert m, (name, extras[0])                 # a JS string literal: nothing appended
        kinds.add(m.group(1))
        assert extras[1:] == ["'com.termux.RUN_COMMAND_STDIN:' + JSON.stringify(ev)",
                              "'com.termux.RUN_COMMAND_BACKGROUND:true'"]
        assert "RUN_COMMAND_ARGUMENTS" not in js and "shell(" not in js and "eval(" not in js
    assert kinds == set(eb.KINDS)


def test_import_script_installs_a_wrapper_per_kind():
    src = IMPORT.read_text()
    m = re.search(r'^KINDS="([^"]*)"', src, re.M)
    assert m and m.group(1).split() == list(eb.KINDS)
    assert "event-publish.sh" in src and "XDG_CONFIG_HOME" in src


def test_no_secret_like_strings_in_the_xml():
    xml = dry_run("--allow", "org.example.app", "--car-bt", "Car")
    assert not re.search(r"(?i)token|passw|secret|api[_-]?key|bearer|-P\s|mosquitto_pub", xml)
    assert not re.search(r"[A-Za-z0-9+/_=-]{40,}", xml)


@pytest.mark.parametrize("args", [["--allow", "not a package"], ["--allow", "x"],
                                  ["--allow", "a.b;rm"], ["--car-bt", "a\nb"], ["--car-bt", "x" * 65]])
def test_builder_refuses_odd_options(args):
    p = subprocess.run(["python3", "-I", str(BUILDER), "--dry-run", *args], capture_output=True, text=True)
    assert p.returncode != 0


def test_out_file_needs_prj_xml(tmp_path):
    ok = subprocess.run(["python3", "-I", str(BUILDER), str(tmp_path / "e.prj.xml")], capture_output=True, text=True)
    assert ok.returncode == 0 and ET.parse(tmp_path / "e.prj.xml").getroot().tag == "TaskerData"
    bad = subprocess.run(["python3", "-I", str(BUILDER), str(tmp_path / "e.xml")], capture_output=True, text=True)
    assert bad.returncode != 0


# ----------------------------------------------------------------------------- task JS in node
def run_task(task, pre="", *args):
    js = scripts(ET.fromstring(dry_run(*args)))[task][0]
    code = ("var calls = [], G = {BATT: '14'};\n"
            "function sendIntent() { calls.push(Array.prototype.slice.call(arguments)); }\n"
            "function global(n) { return G[n]; }\n" + pre + "\n(function () {\n" + js +
            "\n})();\nconsole.log(JSON.stringify(calls));")
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def the_event(calls, kind):
    assert len(calls) == 1
    extras = calls[0][7]
    assert extras[0] == f"com.termux.RUN_COMMAND_PATH:{eb.TERMUX_TASKER}/event-{kind}"
    assert extras[1].startswith("com.termux.RUN_COMMAND_STDIN:") and len(extras) == 3
    ev = json.loads(extras[1].split(":", 1)[1])
    assert set(ev) == {"kind", "source_pkg", "title", "text", "when", "id"} and ev["kind"] == kind
    assert "\n" not in extras[1]
    return ev


@node
def test_notification_from_the_allowlist_goes_only_to_stdin():
    pre = (f"var anpackage = {json.dumps(GEMINI)}, antitle = 'Gemini', antext = {json.dumps(EVIL)},"
           " ankey = '0|bard|7';")
    calls = run_task("EB Notification", pre)
    ev = the_event(calls, "notification")
    assert (ev["source_pkg"], ev["title"], ev["text"], ev["id"]) == (GEMINI, "Gemini", EVIL, "0|bard|7")
    assert isinstance(ev["when"], int)
    assert "touch" not in calls[0][7][0] and "touch" not in calls[0][7][2]
    assert not any("touch" in str(a) for a in calls[0][:7])


@node
@pytest.mark.parametrize("pre", [
    "var anpackage = 'com.whatsapp', antitle = 'x', antext = 'y';",            # not allowlisted
    "var anpackage = 'com.google.android.apps.bard.evil', antext = 'y';",      # prefix only
    "var anpackage = '%anpackage', antext = 'y';",                             # unresolved
    "",                                                                         # unset
])
def test_notification_outside_the_allowlist_is_not_sent(pre):
    assert run_task("EB Notification", pre) == []


@node
def test_allow_option_extends_the_allowlist():
    pre = "var anpackage = 'org.example.app', antitle = '%antitle', antext = 'hi';"
    ev = the_event(run_task("EB Notification", pre, "--allow", "org.example.app"), "notification")
    assert ev["title"] == "" and ev["text"] == "hi"     # unresolved %antitle becomes ""


@node
def test_long_notification_text_is_cut():
    pre = f"var anpackage = {json.dumps(GEMINI)}, antitle = {json.dumps('t' * 900)}, antext = {json.dumps('x' * 5000)};"
    ev = the_event(run_task("EB Notification", pre), "notification")
    assert len(ev["title"]) == 500 and len(ev["text"]) == 2000


@node
@pytest.mark.parametrize("task,kind,title,text", [
    ("EB Battery Low", "battery", "Battery low", "14 %"),
    ("EB Charging", "battery", "Charging", "14 %"),
    ("EB Not Charging", "battery", "Not charging", "14 %"),
    ("EB Car BT Connected", "bluetooth", "Bluetooth connected", ""),
    ("EB Car BT Disconnected", "bluetooth", "Bluetooth disconnected", ""),
])
def test_battery_and_bluetooth_events(task, kind, title, text):
    ev = the_event(run_task(task), kind)
    assert (ev["source_pkg"], ev["title"], ev["text"]) == ("android", title, text)


@node
def test_all_task_scripts_parse():
    for name, js in scripts(ET.fromstring(dry_run())).items():
        p = subprocess.run(["node", "--check", "-"], input="function __t(){\n" + js[0] + "\n}\n",
                           capture_output=True, text=True)
        assert p.returncode == 0, (name, p.stderr)


# ----------------------------------------------------------------------------- event-publish.sh
@pytest.fixture
def stub(tmp_path):
    """mosquitto_pub stub on PATH: argv (NUL-separated) and stdin go to files in tmp_path."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    s = bindir / "mosquitto_pub"
    s.write_text('#!/bin/sh\nprintf \'%s\\0\' "$@" > "$STUB_DIR/argv"\ncat > "$STUB_DIR/stdin"\n')
    s.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()

    def run(args, data, env_extra=None):
        env = {k: v for k, v in os.environ.items() if k != "MQTT_HOST"}
        env.update(PATH=f"{bindir}:{os.environ['PATH']}", STUB_DIR=str(tmp_path), **(env_extra or {}))
        if isinstance(data, str):
            data = data.encode()
        p = subprocess.run([str(PUBLISH), *args], input=data, capture_output=True, env=env,
                           cwd=work, timeout=30)
        argv = (tmp_path / "argv").read_bytes().decode().split("\0")[:-1] if (tmp_path / "argv").exists() else None
        inp = (tmp_path / "stdin").read_bytes().decode() if (tmp_path / "stdin").exists() else None
        return p, argv, inp, work
    return run


def test_publish_compacts_pretty_json_to_one_line(stub):
    p, argv, inp, _ = stub(["notification"], json.dumps(EVENT, indent=2, ensure_ascii=False) + "\n")
    assert p.returncode == 0, p.stderr
    assert argv == ["-h", "127.0.0.1", "-t", "phone/events/notification", "-s"]
    assert "\n" not in inp and json.loads(inp) == EVENT
    assert "Výzkum je hotový" in inp                       # diacritics kept, not \u-escaped
    assert inp == json.dumps(EVENT, ensure_ascii=False, separators=(",", ":"))


def test_mqtt_host_from_the_environment(stub):
    p, argv, _, _ = stub(["notification"], json.dumps(EVENT), {"MQTT_HOST": "10.0.0.7"})
    assert p.returncode == 0 and argv[:2] == ["-h", "10.0.0.7"] and "-r" not in argv


def test_injection_text_is_data_on_stdin_only(stub):
    ev = dict(EVENT, title="$(touch x1) `touch x2`", text=EVIL, id="$(touch x3)")
    p, argv, inp, work = stub(["notification"], json.dumps(ev))
    assert p.returncode == 0, p.stderr
    assert list(work.iterdir()) == []                       # nothing was executed
    assert not any("touch" in a for a in argv)
    assert json.loads(inp) == ev and "$(touch x1) `touch x2`" in inp


def test_line_separators_are_escaped_so_the_subscriber_sees_one_line(stub):
    ev = dict(EVENT, text="a b c\x85d\re\nf")
    p, _, inp, _ = stub(["notification"], json.dumps(ev, ensure_ascii=False))
    assert p.returncode == 0
    assert len(inp.splitlines()) == 1 and json.loads(inp) == ev


@pytest.mark.parametrize("kind", ["#", "+", "a/b", "../x", "", "Notification", "UP", "a b",
                                  "a\nb", "x" * 33, "phone/events/#", "é", "a\tb"])
def test_bad_kinds_are_refused_before_publishing(stub, kind):
    p, argv, inp, _ = stub([kind], json.dumps(dict(EVENT, kind=kind)))
    assert p.returncode != 0 and b"kind not allowed" in p.stderr
    assert argv is None and inp is None


@pytest.mark.parametrize("args", [[], ["notification", "extra"]])
def test_usage(stub, args):
    p, argv, _, _ = stub(args, json.dumps(EVENT))
    assert p.returncode != 0 and b"usage" in p.stderr and argv is None


@pytest.mark.parametrize("data", [
    "", "not json", "{", "[1, 2]", '"text"', "42", "null",
    json.dumps(EVENT) + "\n" + json.dumps(EVENT),                  # two events
    json.dumps({k: v for k, v in EVENT.items() if k != "id"}),     # missing key
    json.dumps(dict(EVENT, kind="download")),                       # kind != topic
    json.dumps(EVENT).replace("1764150000000", "NaN"),
    '{"kind": "notification", "kind": "notification", "source_pkg": "", "title": "", "text": "", "when": 0, "id": ""}',
    b'{"kind": "notification", "source_pkg": "", "title": "\xff", "text": "", "when": 0, "id": ""}',
    '{"kind":"notification","source_pkg":"","title":"\\ud800","text":"","when":0,"id":""}',
    "x" * 70000,
])
def test_invalid_events_are_refused(stub, data):
    p, argv, inp, _ = stub(["notification"], data)
    assert p.returncode != 0 and b"nothing published" in p.stderr
    assert argv is None and inp is None
    if isinstance(data, str) and "Výzkum" in data:
        assert "Výzkum".encode() not in p.stderr               # the event text is not echoed


def test_missing_mosquitto_pub_is_an_error(tmp_path):
    env = dict(os.environ, PATH="/usr/bin:/bin")
    if shutil.which("mosquitto_pub", path=env["PATH"]):
        pytest.skip("a real mosquitto_pub is installed")
    p = subprocess.run([str(PUBLISH), "notification"], input=json.dumps(EVENT).encode(),
                       capture_output=True, env=env, timeout=30)
    assert p.returncode != 0 and b"mosquitto_pub is not installed" in p.stderr


def test_publisher_source_rules():
    src = PUBLISH.read_text()
    assert src.startswith("#!/usr/bin/env bash\n") and "set -euo pipefail" in src
    assert os.access(PUBLISH, os.X_OK)
    assert "eval" not in src and " -m " not in src and " -r" not in src
    assert 'mosquitto_pub -h "${MQTT_HOST:-127.0.0.1}" -t "phone/events/$kind" -s' in src


@pytest.mark.parametrize("script", [PUBLISH, IMPORT])
def test_scripts_parse(script):
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


def test_example_acl_separates_publisher_and_agent():
    users, cur = {None: []}, None
    for line in (MQTT / "acl.example").read_text().splitlines():
        line = line.split("#", 1)[0].strip() if not line.lstrip().startswith("topic") else line.strip()
        if line.startswith("user "):
            cur = line.split()[1]
            users[cur] = []
        elif line.startswith("topic "):
            users[cur].append(tuple(line.split()[1:]))
    assert users["phone-tasker"] == [("write", "phone/events/#")]
    assert ("read", "phone/events/#") in users["phone-agent"]
    assert not any(t.startswith("phone/") and a != "read" for a, t in users["phone-agent"])
    assert not any(t.startswith("phone/") for _, t in users[None])     # anonymous: no events
    assert ("readwrite", "mia/#") in users[None]                        # mia/* keeps working
    conf = (MQTT / "event-bus.conf.example").read_text()
    assert "listener 1883 127.0.0.1" in conf and "allow_anonymous true" in conf
