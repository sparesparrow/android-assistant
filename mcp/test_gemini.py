"""Gemini driver (gemini.py), ask_gemini / gemini_reply / phone_command validation and the
Tasker agents builder. No phone: droid is stubbed with recorded screen dumps (fixtures/gemini).
Run:  .venv/bin/python -m pytest -q test_gemini.py
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import gemini  # noqa: E402

FIX = HERE / "fixtures" / "gemini"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.mark.parametrize("given,key", [
    ("", ""), ("+Deep Research", "deep_research"), ("deep research", "deep_research"),
    ("Generate Image", "image"), ("+image", "image"), ("Vytvořit obrázky", "image"),
    ("Videos", "video"), ("+Music", "music"), ("Canvas", "canvas"),
    ("Učení s vedením", "guided_learning"), ("guided_learning", "guided_learning"),
])
def test_resolve_tool(given, key):
    assert gemini.resolve_tool(given) == key


def test_unknown_tool_lists_choices():
    with pytest.raises(gemini.GeminiError, match="deep research"):
        gemini.resolve_tool("+Teleport")


def test_reply_after_prompt_without_footer_and_done_marker():
    ns = load("reply-done.json")
    assert gemini.reply_text(ns, "Kolik je 17 krát 23? Odpověz jen číslem.") == "391"
    assert gemini.is_done(ns)


def test_chip_seen_for_selected_deep_research():
    ns = load("deep-research-chip.json")
    assert gemini.selected_tool_chip(ns, "deep_research")
    assert not gemini.selected_tool_chip(ns, "image")


def test_clickable_parent_of_label():
    ns = load("deep-research-chip.json")
    send = gemini.find(ns, gemini.SEND_LABELS, exact=True)
    assert not send["click"] and gemini.clickable_at(ns, send) == (930, 1223)


def test_read_aloud_taps_lowest_play(monkeypatch):
    ns = load("reply-done.json")
    taps = []
    monkeypatch.setattr(gemini, "tap", lambda x, y: taps.append((x, y)))
    assert gemini.read_aloud(ns) and taps == [(972, 653)]
    assert not gemini.read_aloud([n for n in ns if gemini.label(n) != "prehrat"])


def test_ask_flow_sends_and_confirms_share(monkeypatch):
    """new chat -> send -> 'share content from another app?' -> Sdílet -> reply."""
    prompt = "Kolik je 17 krát 23? Odpověz jen číslem."
    composer = [{"i": 0, "text": prompt, "desc": "", "id": "x_input", "cls": "EditText",
                 "pkg": "com.google.android.googlequicksearchbox", "click": True, "edit": True,
                 "x": 490, "y": 1345},
                {"i": 1, "text": "", "desc": "Odeslat", "id": "", "cls": "View",
                 "pkg": "com.google.android.googlequicksearchbox", "click": False, "edit": False,
                 "x": 930, "y": 1345}]
    share = [{"i": 0, "text": "Zrušit", "desc": "", "id": "", "cls": "Button",
              "pkg": "com.google.android.googlequicksearchbox", "click": True, "edit": False,
              "x": 600, "y": 1264},
             {"i": 1, "text": "Sdílet", "desc": "", "id": "", "cls": "Button",
              "pkg": "com.google.android.googlequicksearchbox", "click": True, "edit": False,
              "x": 853, "y": 1264}]
    screens = iter([composer, share, load("reply-done.json"), load("reply-done.json")])
    cmds, taps = [], []
    monkeypatch.setattr(gemini, "_droid", lambda *a, **k: cmds.append(a) or "")
    monkeypatch.setattr(gemini, "nodes", lambda: next(screens))
    monkeypatch.setattr(gemini, "tap", lambda x, y: taps.append((x, y)))
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    r = gemini.ask(prompt, wait_s=5, log=lambda m: None)
    assert r["sent"] and r["reply"] == "391"
    assert taps == [(930, 1345), (853, 1264)]
    assert "android.intent.action.SEND" in cmds[0][1] and "'Kolik je 17" in cmds[0][1]


def test_answer_share_button_is_not_the_share_consent(monkeypatch):
    """Real dump, 0.7 s after Send (2026-11-27): the answer's action row has a "Sdílet" Button,
    which makes a public link to the chat and opens the share sheet. send() must not tap it."""
    ns = load("reply-share-row.json")
    assert any(n["cls"] == "Button" and gemini.label(n) == "sdilet" for n in ns)
    assert gemini.share_consent(ns) is None
    composer = [{"i": 0, "text": "", "desc": "Odeslat", "id": "", "cls": "View", "pkg": "p", "x": 930, "y": 1345}]
    taps = []
    monkeypatch.setattr(gemini, "nodes", lambda: ns)
    monkeypatch.setattr(gemini, "tap", lambda x, y: taps.append((x, y)))
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    gemini.send(composer)
    assert taps == [(930, 1345)]


def test_share_consent_needs_a_dismiss_button_on_its_row():
    ok = {"text": "Share", "cls": "Button", "y": 1264}
    assert gemini.share_consent([ok, {"text": "Cancel", "cls": "Button", "y": 1264}]) is ok
    assert gemini.share_consent([ok]) is None
    assert gemini.share_consent([ok, {"text": "Cancel", "cls": "Button", "y": 400}]) is None
    assert gemini.share_consent([{"text": "Share", "cls": "View", "y": 1}, {"text": "Cancel", "cls": "Button", "y": 1}]) is None


def test_prompt_quote_is_shell_safe(monkeypatch):
    cmds = []
    monkeypatch.setattr(gemini, "_droid", lambda *a, **k: cmds.append(a) or "")
    monkeypatch.setattr(gemini, "wait_for", lambda pred, **k: [])
    with pytest.raises(gemini.GeminiError):
        gemini.new_chat_with("it's'; reboot; '")
    assert "'it'\\''s'\\''; reboot; '\\'''" in cmds[0][1]


def test_empty_and_huge_prompt_refused():
    with pytest.raises(gemini.GeminiError):
        gemini.ask("   ")
    with pytest.raises(gemini.GeminiError):
        gemini.ask("x" * (gemini.PROMPT_MAX + 1))


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_STATE", str(tmp_path))
    import server
    server = importlib.reload(server)
    calls = []
    monkeypatch.setattr(server, "_run", lambda cmd, timeout=30, inp=None: calls.append(cmd) or (0, "ok"))
    server.calls = calls
    return server


def test_ask_gemini_rejects_unknown_tool_before_touching_phone(srv):
    assert "unknown Gemini tool" in srv.ask_gemini("hi", tool="+Teleport")
    assert not srv.calls


@pytest.mark.parametrize("action,arg,seconds,expect", [
    ("open_file", "/etc/passwd", 1, "under /sdcard"),
    ("open_file", "/sdcard/../etc/passwd", 1, "under /sdcard"),
    ("volume", "99", 1, "0..15"),
    ("volume", "3; reboot", 1, "0..15"),
    ("rm -rf", "", 1, "unknown action"),
])
def test_phone_command_validation(srv, action, arg, seconds, expect):
    assert expect in srv.phone_command(action, arg, seconds)
    assert not srv.calls


def test_phone_command_open_file_is_quoted(srv):
    srv.phone_command("open_file", "/sdcard/a b'c.pdf")
    assert srv.calls[-1][-1] == "termux-open '/sdcard/a b'\\''c.pdf'"


def test_tasker_agents_builder(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "agents_builder", HERE.parent / "phone" / "tasker" / "build-claude-agents.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    names = b.agents()
    assert {"gemini", "phone"} <= set(names)
    xml = b.to_xml(b.build(names))
    assert "<nme>Agent gemini</nme>" in xml and "agent-gemini" in xml
    assert "RUN_COMMAND_STDIN:' + p" in xml        # prompt via stdin, never a command line
    (tmp_path / "bad name.md").write_text("x")
    with pytest.raises(ValueError):
        b.agents(tmp_path)


def test_tasker_agents_builder_ai_apps(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "agents_builder", HERE.parent / "phone" / "tasker" / "build-claude-agents.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    apps = {"deepseek": "+DeepThink", "kimi": "+Thinking", "grok": "+DeepSearch",
            "chatgpt": "+Deep Research", "perplexity": "+Research"}
    for n in apps:
        (tmp_path / f"{n}.md").write_text("x")
    names = b.agents(tmp_path)
    assert names == sorted(apps)
    xml = b.to_xml(b.build(names))
    for n, mode in apps.items():
        title, hint = b.HINTS[n]
        assert title.startswith("Ask ") and mode in hint
        assert f"<nme>Agent {n}</nme>" in xml and f"tasker/agent-{n}" in xml and title in xml


def _n(i, text, x, y, cls="ViewGroup", click=False, desc=""):
    return {"i": i, "text": text, "desc": desc, "id": "", "cls": cls, "click": click, "edit": False,
            "pkg": "com.google.android.googlequicksearchbox", "x": x, "y": y}


def test_start_button_is_the_right_of_two_unlabeled_plan_buttons():
    # recorded layout of the Deep Research plan card, Nov 2026 (labels drawn, not exposed)
    ns = [_n(0, "I've put together a research plan.", 540, 609, "TextView"),
          _n(1, "Research plan with 3 steps", 540, 1275),
          _n(2, "", 579, 1707, click=True), _n(3, "", 839, 1707, click=True),
          _n(4, "", 102, 1882, "Button", True, desc="Dobrá odpověď")]
    b = gemini.start_button(ns)
    assert (b["x"], b["y"]) == (839, 1707)


def test_start_button_prefers_a_label_and_needs_the_plan_card():
    assert gemini.start_button([_n(0, "", 579, 1707, click=True), _n(1, "", 839, 1707, click=True)]) is None
    ns = [_n(0, "Research plan with 3 steps", 540, 1275), _n(1, "Start research", 700, 1700, "Button", True)]
    assert gemini.start_button(ns)["text"] == "Start research"
