"""Offline tests for the android-control `droid` script: non-ASCII typing without Termux:API."""
import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

DROID = Path(__file__).resolve().parent.parent / "skill" / "android-control" / "scripts" / "droid"


@pytest.fixture
def d(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_loader("droid", importlib.machinery.SourceFileLoader("droid", str(DROID)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "TERMUX_BIN", str(tmp_path))          # no termux-clipboard-set
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod


def node(**kw):
    base = {"text": "", "desc": "", "id": "", "cls": "", "pkg": "app", "click": False, "edit": False}
    return {**base, **kw}


COPY = node(id="copy", pkg="com.android.intentresolver", click=True, x=970, y=1442)
FIELD_UP = node(id="input", edit=True, focused=True, x=540, y=760)       # keyboard shown
FIELD_DOWN = node(id="input", edit=True, x=540, y=1162)                  # keyboard dropped


def run_type(d, monkeypatch, screens, fg="app/.Main"):
    cmds = []
    monkeypatch.setattr(d, "sh", lambda c, *a, **k: cmds.append(c) or "")
    screens = iter(screens)
    monkeypatch.setattr(d, "dump_nodes", lambda: next(screens))
    monkeypatch.setattr(d, "foreground", lambda: fg if isinstance(fg, str) else fg.pop(0))
    d.cmd_type(["Příliš", "žluťoučký", "kůň"])
    return cmds


def test_non_ascii_without_termux_api_copies_via_share_sheet_and_refocuses(d, monkeypatch):
    cmds = run_type(d, monkeypatch, [[FIELD_UP], [COPY], [COPY], [FIELD_DOWN]])
    chooser = [c for c in cmds if "CHOOSER" in c]
    assert len(chooser) == 1 and "P%C5%99%C3%ADli%C5%A1%20" in chooser[0]
    assert "'" not in chooser[0].split("INTENT '")[1][:-1]                # text cannot break the quoting
    taps = [c for c in cmds if c.startswith("input tap")]
    assert taps == ["input tap 970 1442", "input tap 540 1162"]          # Copy, then the field where it is now
    assert cmds[-1] == "input keyevent 279"
    assert "input keyevent 4" not in cmds


def test_copy_is_tapped_only_once_it_stops_moving(d, monkeypatch):
    moving = dict(COPY, y=1700)
    cmds = run_type(d, monkeypatch, [[FIELD_UP], [moving], [COPY], [COPY], [FIELD_DOWN]])
    assert "input tap 970 1700" not in cmds and "input tap 970 1442" in cmds


def test_quick_share_is_backed_out_of_and_retried(d, monkeypatch):
    fg = ["com.google.android.gms/.nearby.sharing.main.MainActivity", "app/.Main"]
    cmds = run_type(d, monkeypatch, [[FIELD_UP], [COPY], [COPY], [COPY], [COPY], [FIELD_DOWN]], fg=fg)
    assert sum("CHOOSER" in c for c in cmds) == 2
    assert cmds.count("input keyevent 4") == 1


def test_never_taps_anything_but_copy(d, monkeypatch):
    person = node(id="item", pkg="com.android.intentresolver", click=True, x=108, y=1858)
    monkeypatch.setattr(d.time, "time", iter(range(0, 1000, 5)).__next__)  # each read: 5 s later
    with pytest.raises(SystemExit, match="share sheet"):
        run_type(d, monkeypatch, [[FIELD_UP]] + [[person]] * 20)


def test_refind_field_does_not_guess_between_several(d):
    a, b = node(edit=True, x=1, y=1), node(edit=True, x=2, y=2)
    assert d.refind_field(node(edit=True), [a, b]) is None
    assert d.refind_field(node(edit=True, id="x"), [dict(a, id="x"), b]) == dict(a, id="x")


def test_ascii_still_uses_input_text(d, monkeypatch):
    cmds = []
    monkeypatch.setattr(d, "sh", lambda c, *a, **k: cmds.append(c) or "")
    d.cmd_type(["it's", "fine"])
    assert cmds == ["input text 'it'\\''s%sfine'"]
