"""ask_ai / ai_reply MCP tools (server.py over chatbots.py). No phone: server._run is stubbed and
chatbots.ask / chatbots.reply are monkeypatched.
Run:  .venv/bin/python -m pytest -q test_ai_tools.py
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import chatbots  # noqa: E402
import gemini  # noqa: E402


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_STATE", str(tmp_path))
    import server
    server = importlib.reload(server)
    calls = []
    monkeypatch.setattr(server, "_run", lambda cmd, timeout=30, inp=None: calls.append(cmd) or (0, "ok"))
    server.calls = calls
    return server


@pytest.fixture()
def asked(monkeypatch):
    seen = []

    def fake(app, prompt, mode="", send_it=True, wait_s=0, log=print, dump=None):
        seen.append({"app": app, "prompt": prompt, "mode": mode, "send_it": send_it, "wait_s": wait_s})
        return {"app": app, "mode": mode, "sent": send_it, "reply": "ok"}

    monkeypatch.setattr(chatbots, "ask", fake)
    return seen


def test_unknown_app_errors_before_touching_phone(srv, asked):
    out = srv.ask_ai("clippy", "hi", return_to_previous=True)
    assert "unknown chat app" in out
    assert not srv.calls and not asked


def test_unknown_mode_errors_before_touching_phone(srv, asked):
    out = srv.ask_ai("deepseek", "hi", mode="+Teleport", return_to_previous=True)
    assert "unknown deepseek mode" in out
    assert not srv.calls and not asked


@pytest.mark.parametrize("given,want", [(999, 300), (-5, 0), (42, 42)])
def test_wait_is_clamped(srv, asked, given, want):
    r = json.loads(srv.ask_ai("grok", "hi", wait_s=given))
    assert r["reply"] == "ok"
    assert asked[-1]["wait_s"] == want


@pytest.mark.parametrize("app,mode,key", [("chatgpt", "deep research", "deep_research"),
                                          ("gpt", "+Deep Research", "deep_research"),
                                          ("perplexity", "research", "research")])
def test_long_running_modes_do_not_wait(srv, asked, app, mode, key):
    srv.ask_ai(app, "hi", mode=mode, wait_s=120)
    assert asked[-1]["mode"] == key and asked[-1]["wait_s"] == 0


def test_other_modes_keep_wait_and_send_flag(srv, asked):
    srv.ask_ai("perplexity", "hi", mode="pro", send=False, wait_s=30)
    assert asked[-1] == {"app": "perplexity", "prompt": "hi", "mode": "pro", "send_it": False,
                         "wait_s": 30}


def test_gemini_error_becomes_json_error(srv, monkeypatch):
    def boom(*a, **kw):
        raise chatbots.ChatbotError("no send button in the kimi composer")
    monkeypatch.setattr(chatbots, "ask", boom)
    assert json.loads(srv.ask_ai("kimi", "hi")) == {"error": "no send button in the kimi composer"}

    def boom2(*a, **kw):
        raise gemini.GeminiError("adb offline")
    monkeypatch.setattr(chatbots, "ask", boom2)
    assert json.loads(srv.ask_ai("kimi", "hi")) == {"error": "adb offline"}


def test_return_to_previous_restores_app(srv, asked, monkeypatch):
    monkeypatch.setattr(srv, "_run", lambda cmd, timeout=30, inp=None:
                        srv.calls.append(cmd) or (0, "com.termux/.app.TermuxActivity"))
    srv.ask_ai("deepseek", "hi", return_to_previous=True)
    assert srv.calls[-1][-1] == "am start -n com.termux/.app.TermuxActivity"


def test_ai_reply_passes_through(srv, monkeypatch):
    slept = []
    monkeypatch.setattr(srv.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(chatbots, "reply", lambda app: {"app": app, "reply": "42", "generating": False})
    assert json.loads(srv.ai_reply("grok", wait_s=999)) == {"app": "grok", "reply": "42",
                                                             "generating": False}
    monkeypatch.setattr(chatbots, "reply", lambda app: f"{app} is not on screen")
    assert srv.ai_reply("kimi", wait_s=-3) == "kimi is not on screen"
    assert slept == [300, 0]


def test_annotations(srv):
    tools = {t.name: t for t in srv.mcp._tool_manager.list_tools()}
    assert tools["ai_reply"].annotations.read_only_hint is True
    assert tools["ask_ai"].annotations.read_only_hint is False
    assert tools["ask_ai"].annotations.open_world_hint is True


@pytest.fixture()
def on_screen(srv, monkeypatch):
    """ai_reply with scroll: the app is in front; swipes and the final read land in one log."""
    log = []
    monkeypatch.setattr(srv.time, "sleep", lambda s: None)
    monkeypatch.setattr(chatbots, "nodes", lambda: [{"pkg": chatbots.PROFILES["kimi"]["pkg"]}])
    monkeypatch.setattr(srv, "_run", lambda cmd, timeout=30, inp=None: log.append(cmd[1:]) or (0, "ok"))
    monkeypatch.setattr(chatbots, "reply", lambda app: log.append(["reply", app])
                        or {"app": app, "reply": "more", "generating": False})
    return log


@pytest.mark.parametrize("given,swipes", [(0, 0), (1, 1), (2, 2), (99, 5), (-3, 0)])
def test_ai_reply_scroll_swipes_up_bounded_then_reads(srv, on_screen, given, swipes):
    r = json.loads(srv.ai_reply("kimi", scroll=given))
    assert r["reply"] == "more"
    assert on_screen == [["swipe", "up"]] * swipes + [["reply", "kimi"]]
    assert swipes <= srv.SCROLL_MAX


def test_ai_reply_scroll_never_swipes_outside_the_app(srv, on_screen, monkeypatch):
    assert "unknown chat app" in srv.ai_reply("clippy", scroll=2)
    monkeypatch.setattr(chatbots, "nodes", lambda: [{"pkg": "com.termux"}])
    assert srv.ai_reply("grok", scroll=2) == "grok is not on screen; open it (or ask it something) first"
    assert on_screen == []


def test_reply_tools_offer_scroll_instead_of_act(srv):
    tools = {t.name: t for t in srv.mcp._tool_manager.list_tools()}
    for name in ("ai_reply", "gemini_reply"):
        assert "scroll" in tools[name].parameters["properties"]
        assert "`act`" not in tools[name].description
