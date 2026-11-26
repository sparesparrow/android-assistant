"""phone/event-pub.py: normalise a phone event and publish it on phone/events/<kind>. No broker."""
import importlib.util
import io
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "event_pub", Path(__file__).parent.parent / "phone" / "event-pub.py")
ep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ep)


def test_build_event_keeps_the_schema_fields_only():
    ev = ep.build_event("notification", source_pkg="com.x", title="T", text="body", id="k", when=5)
    assert ev == {"kind": "notification", "source_pkg": "com.x", "title": "T", "text": "body",
                  "when": 5, "id": "k"}


def test_build_event_defaults_when_and_drops_empty_fields():
    ev = ep.build_event("battery", text="15%")
    assert set(ev) == {"kind", "text", "when"} and isinstance(ev["when"], int)


@pytest.mark.parametrize("kind", ["", "Notif", "a/b", "#", "+", "x" * 33])
def test_build_event_rejects_bad_kinds(kind):
    with pytest.raises(ValueError):
        ep.build_event(kind, text="x")


def test_build_event_truncates_long_text():
    assert len(ep.build_event("clip", text="y" * 9000)["text"]) == ep.TEXT_MAX + 1


def test_command_is_not_retained_and_uses_the_kind_topic():
    ev = ep.build_event("notification", text="hi", when=1)
    cmd = ep.command(ev, host="127.0.0.1")
    assert cmd[:5] == ["mosquitto_pub", "-h", "127.0.0.1", "-t", "phone/events/notification"]
    assert "-r" not in cmd and json.loads(cmd[cmd.index("-m") + 1]) == ev


def test_from_notif_wait_maps_its_json():
    line = json.dumps({"pkg": "com.google.android.apps.bard", "title": "Done", "text": "Report",
                       "key": "0|bard|1", "when": 1700000000000})
    ev = ep.from_notif_wait(line)
    assert ev == {"kind": "notification", "source_pkg": "com.google.android.apps.bard", "title": "Done",
                  "text": "Report", "when": 1700000000000, "id": "0|bard|1"}


def test_main_dry_run_prints_without_publishing(monkeypatch, capsys):
    monkeypatch.setattr(ep.subprocess, "run", lambda *a, **k: pytest.fail("published"))
    assert ep.main(["notification", "--title", "T", "--text", "x", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "T"


def test_main_stdin_publishes(monkeypatch):
    sent = []
    monkeypatch.setattr(ep.subprocess, "run", lambda cmd, **k: sent.append(cmd) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr(ep.sys, "stdin", io.StringIO('{"pkg": "a.b", "title": "t", "text": "x", "key": "k"}\n'))
    assert ep.main(["--stdin"]) == 0
    assert sent and sent[0][4] == "phone/events/notification"


def test_main_bad_kind_exits_2(capsys):
    assert ep.main(["Bad/Kind", "--text", "x", "--dry-run"]) == 2
