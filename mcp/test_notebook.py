"""Gemini Notebook driver (notebook.py) and the notebook_* tools. No phone: recorded dumps.
Run:  .venv/bin/python -m pytest -q test_notebook.py
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import gemini  # noqa: E402
import notebook as nb  # noqa: E402

NS = json.loads((HERE / "fixtures" / "gemini" / "notebook-reply.json").read_text())
Q = "Ahoj, tohle je test od Claude – odpověz jedním slovem: připraven?"
ID = "cbe9fa1f-9df8-4865-875e-c1b39496a955"


@pytest.mark.parametrize("given", [ID, f"https://notebook.google.com/notebook/{ID}",
                                   f"https://notebooklm.google.com/notebook/{ID}?x=1", ""])
def test_notebook_url_accepts_links_and_ids(given):
    assert nb.notebook_url(given) == f"https://notebook.google.com/notebook/{ID}"


@pytest.mark.parametrize("bad", ["https://evil.example.com/notebook/" + ID, "notebook; reboot",
                                 "https://notebook.google.com/notebook/not-an-id"])
def test_notebook_url_refuses_others(bad):
    with pytest.raises(gemini.GeminiError):
        nb.notebook_url(bad)


def test_reply_title_and_screen_detection():
    assert nb.reply_after(NS, Q) == "Připraven."
    assert nb.title_of(NS) == "Shared agent session"
    assert nb.in_notebook(NS)
    assert "Save to note" not in nb.chat_text(NS)


@pytest.mark.parametrize("given,key", [("+Audio Overview", "audio"), ("podcast", "audio"),
                                       ("Slide Deck", "slides"), ("quiz", "quiz"), ("report", "report")])
def test_resolve_studio(given, key):
    assert nb.resolve_studio(given) == key


def test_unknown_studio_output():
    with pytest.raises(gemini.GeminiError, match="audio, video"):
        nb.resolve_studio("hologram")


def test_clipboard_intent_percent_encodes(monkeypatch):
    cmds = []
    monkeypatch.setattr(gemini, "_droid", lambda *a, **k: cmds.append(a) or "")
    monkeypatch.setattr(gemini, "wait_for", lambda pred, **k: [{"i": 0, "id": "copy", "x": 970, "y": 1442}])
    monkeypatch.setattr(nb, "tap", lambda x, y: cmds.append(("tap", x, y)))
    monkeypatch.setattr(nb.time, "sleep", lambda s: None)
    nb.to_clipboard("žluť'; reboot; '")
    sh = cmds[0][1]
    assert "android.intent.action.CHOOSER" in sh and "'" not in sh.split("--eu android.intent.extra.INTENT '")[1][:-1]
    assert "%C5%BElu%C5%A5%27%3B%20reboot" in sh and cmds[-1] == ("tap", 970, 1442)


def test_save_note_taps_newest(monkeypatch):
    taps = []
    monkeypatch.setattr(nb, "open_notebook", lambda n: NS)
    monkeypatch.setattr(nb, "tab", lambda ns, name: NS)
    send = {"i": 99, "text": "", "desc": "Send", "id": "", "cls": "Button", "pkg": nb.PKG,
            "click": True, "edit": False, "x": 958, "y": 1375}
    monkeypatch.setattr(nb, "to_clipboard", lambda t: None)
    monkeypatch.setattr(nb, "paste_into", lambda ns, t: NS + [send])
    monkeypatch.setattr(nb, "nodes", lambda: NS)
    monkeypatch.setattr(nb, "tap", lambda x, y: taps.append((x, y)))
    monkeypatch.setattr(nb.time, "sleep", lambda s: None)
    r = nb.ask(Q, wait_s=0, save_note=True, log=lambda m: None)
    assert r["reply"] == "Připraven." and r["saved_to_note"]
    assert taps == [(958, 1375), (245, 1558)]          # Save to note under the answer, not the intro card


def test_server_tool_reports_errors_as_json(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_STATE", str(tmp_path))
    import importlib
    import server
    server = importlib.reload(server)
    out = json.loads(server.notebook_ask("q", notebook="https://evil.example.com/x"))
    assert "notebook must be" in out["error"]
    assert "unknown Studio output" in json.loads(server.notebook_studio("hologram"))["error"]
