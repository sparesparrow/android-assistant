"""Generic chat-app driver (chatbots.py): DeepSeek, Kimi, Grok, ChatGPT, Perplexity.
No phone: droid, nodes and tap are stubbed with synthetic screens.
Run:  .venv/bin/python -m pytest -q test_chatbots.py
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import chatbots  # noqa: E402
import gemini  # noqa: E402

FIX = HERE / "fixtures" / "chatbots"


def screen(*specs, pkg=None):
    """Build a node list; each spec is a dict of overrides, `i` is assigned in order."""
    out = []
    for s in specs:
        n = {"text": "", "desc": "", "id": "", "cls": "TextView", "pkg": pkg, "click": False,
             "edit": False, "x": 540, "y": 800}
        n.update(s)
        n["i"] = len(out)
        out.append(n)
    return out


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture()
def phone(monkeypatch):
    """Fake phone: `phone.screens` is consumed one per nodes() call (the last one sticks),
    droid commands and taps are recorded, sleep advances a fake clock."""
    class P:
        screens: list = []
        cmds: list = []
        taps: list = []
        clip: list = []
        now = [1000.0]
        reads = [0]

    def fake_nodes():
        P.reads[0] += 1
        s = P.screens.pop(0) if len(P.screens) > 1 else P.screens[0]
        if isinstance(s, Exception):           # a failed uiautomator dump
            raise s
        return s

    def fake_droid(*a, **k):
        P.cmds.append(a)
        return ""

    for mod in (gemini, chatbots):
        monkeypatch.setattr(mod, "nodes", fake_nodes)
        monkeypatch.setattr(mod, "_droid", fake_droid)
        monkeypatch.setattr(mod, "tap", lambda x, y: P.taps.append((x, y)))
    monkeypatch.setattr(chatbots.time, "time", lambda: P.now[0])
    monkeypatch.setattr(chatbots.time, "sleep", lambda s: P.now.__setitem__(0, P.now[0] + s))
    monkeypatch.setattr(chatbots.notebook, "to_clipboard", lambda t: P.clip.append(t))
    return P


# ------------------------------------------------------------------ resolving
@pytest.mark.parametrize("given,app", [
    ("deepseek", "deepseek"), ("DeepSeek", "deepseek"), (" Deep Seek ", "deepseek"),
    ("kimi", "kimi"), ("Moonshot", "kimi"), ("grok", "grok"), ("x", "grok"), ("xAI", "grok"),
    ("ChatGPT", "chatgpt"), ("chat gpt", "chatgpt"), ("OpenAI", "chatgpt"), ("gpt", "chatgpt"),
    ("Perplexity", "perplexity"), ("pplx", "perplexity"),
])
def test_resolve_app(given, app):
    assert chatbots.resolve_app(given) == app


def test_unknown_app_lists_apps():
    with pytest.raises(chatbots.ChatbotError) as e:
        chatbots.resolve_app("bard9000")
    for a in chatbots.APPS:
        assert a in str(e.value)


def test_chatbot_error_is_gemini_error():
    assert issubclass(chatbots.ChatbotError, gemini.GeminiError)


def test_profiles_cover_apps():
    assert chatbots.APPS == ("deepseek", "kimi", "grok", "chatgpt", "perplexity")
    assert set(chatbots.PROFILES) == set(chatbots.APPS)
    assert chatbots.PROFILES["deepseek"]["share"] == "com.deepseek.chat/.ShareProxyActivity"
    assert chatbots.PROFILES["perplexity"]["pkg"] == "ai.perplexity.app.android"
    for p in chatbots.PROFILES.values():
        assert p["text_entry"] in ("share", "paste")
        assert p["modes"] and p["send_labels"] and p["stop_labels"]


@pytest.mark.parametrize("app,given,key", [
    ("deepseek", "", ""), ("deepseek", "+DeepThink", "deepthink"), ("deepseek", "Hluboké myšlení", "deepthink"),
    ("deepseek", "search", "search"), ("deepseek", "+Hledat", "search"),
    ("kimi", "thinking", "thinking"), ("kimi", "+search", "search"),
    ("grok", "Think", "think"), ("grok", "DeepSearch", "deepsearch"),
    ("chatgpt", "search", "search"), ("chatgpt", "deep research", "deep_research"),
    ("chatgpt", "+Deep_Research", "deep_research"),
    ("perplexity", "Deep research", "research"), ("perplexity", "+research", "research"),
])
def test_resolve_mode(app, given, key):
    assert chatbots.resolve_mode(app, given) == key


def test_unknown_mode_lists_that_apps_modes():
    with pytest.raises(chatbots.ChatbotError) as e:
        chatbots.resolve_mode("grok", "+teleport")
    assert "think" in str(e.value) and "deepsearch" in str(e.value)
    assert "deepthink" not in str(e.value)


def test_prompt_limits(phone):
    with pytest.raises(chatbots.ChatbotError, match="empty"):
        chatbots.ask("deepseek", "   ")
    with pytest.raises(chatbots.ChatbotError, match=str(chatbots.PROMPT_MAX)):
        chatbots.ask("deepseek", "x" * (chatbots.PROMPT_MAX + 1))
    with pytest.raises(chatbots.ChatbotError):
        chatbots.ask("nosuchapp", "hi")
    with pytest.raises(chatbots.ChatbotError):
        chatbots.ask("deepseek", "hi", mode="+teleport")
    assert not phone.cmds and not phone.taps     # validated before touching the phone
    assert chatbots.PROMPT_MAX == 8000


# ------------------------------------------------------------------ reading
DS = chatbots.PROFILES["deepseek"]["pkg"]
PROMPT = "Kolik je 17 krát 23? Odpověz jen číslem."


def deepseek_reply_screen(answer="391", generating=False):
    specs = [
        {"text": "DeepSeek", "y": 120},                                     # header, above `top`
        {"text": "Nový chat", "y": 400, "cls": "Button", "click": True},
        {"text": PROMPT, "y": 500},
        {"text": answer, "y": 700},
        {"text": "AI-generated, for reference only", "y": 1900},          # footer
        {"text": "", "desc": "Copy", "y": 760, "click": True},
        {"text": "Zpráva DeepSeek", "cls": "EditText", "edit": True, "click": True, "y": 2100},
    ]
    if generating:
        specs.append({"desc": "Stop", "cls": "Button", "click": True, "x": 980, "y": 2100})
    return screen(*specs, pkg=DS)


def test_reply_text_strips_echo_chrome_and_footer():
    ns = deepseek_reply_screen()
    assert chatbots.reply_text("deepseek", ns, PROMPT) == "391"
    text = chatbots.chat_text("deepseek", ns)
    assert PROMPT in text and "391" in text
    assert "DeepSeek" not in text.split("\n")[0] and "AI-generated" not in text
    assert "Zpráva DeepSeek" not in text and "Copy" not in text


def test_reply_text_without_echo_is_whole_chat():
    ns = [n for n in deepseek_reply_screen() if n["text"] != PROMPT]
    assert chatbots.reply_text("deepseek", ns, PROMPT) == "391"


def test_reply_text_ignores_other_apps():
    ns = deepseek_reply_screen() + screen({"text": "notification noise", "y": 900},
                                          pkg="com.android.systemui")
    assert "noise" not in chatbots.reply_text("deepseek", ns, PROMPT)


def test_in_app_and_is_generating():
    ns = deepseek_reply_screen(generating=True)
    assert chatbots.in_app("deepseek", ns)
    assert not chatbots.in_app("grok", ns)
    assert chatbots.is_generating("deepseek", ns)
    assert not chatbots.is_generating("deepseek", deepseek_reply_screen())


def test_reply_reads_screen(phone):
    phone.screens = [deepseek_reply_screen()]
    r = chatbots.reply("deepseek")
    assert isinstance(r, dict) and r["app"] == "deepseek" and "391" in r["reply"]
    assert r["generating"] is False


def test_reply_when_app_not_in_front(phone):
    phone.screens = [screen({"text": "Home"}, pkg="com.android.launcher3")]
    r = chatbots.reply("kimi")
    assert isinstance(r, str) and "not" in r


# ------------------------------------------------------------------ ask flow
@pytest.fixture()
def readable_composer(monkeypatch):
    """The generic paste-fallback tests use the deepseek profile with a readable EditText;
    the real DeepSeek composer is hidden from uiautomator (see the deepseek fixture tests)."""
    monkeypatch.setitem(chatbots.PROFILES["deepseek"], "composer_hidden", False)


def composer_screen(text, mode_label="DeepThink", send=True):
    specs = [
        {"text": mode_label, "cls": "Button", "click": True, "x": 150, "y": 2000},
        {"text": text, "cls": "EditText", "edit": True, "click": True, "x": 500, "y": 2100},
    ]
    if send:
        specs.append({"desc": "Send", "cls": "Button", "click": True, "x": 980, "y": 2100})
    return screen(*specs, pkg=DS)


def test_ask_happy_path_with_mode_and_wait(phone, tmp_path):
    phone.screens = [
        composer_screen(PROMPT),                  # share intent opened a chat with the prompt
        composer_screen(PROMPT),                  # after tapping DeepThink
        deepseek_reply_screen("39", generating=True),   # right after Send
        deepseek_reply_screen("39", generating=True),   # wait loop: still generating
        deepseek_reply_screen("391"),
        deepseek_reply_screen("391"),             # unchanged -> done
    ]
    r = chatbots.ask("DeepSeek", PROMPT, mode="+deepthink", wait_s=60, log=lambda m: None,
                     dump=str(tmp_path))
    assert r["app"] == "deepseek" and r["mode"] == "deepthink" and r["sent"]
    assert r["reply"] == "391"
    assert "sources" not in r
    share = phone.cmds[0]
    assert share[0] == "sh" and "android.intent.action.SEND" in share[1]
    assert "com.deepseek.chat/.ShareProxyActivity" in share[1] and "'Kolik je 17" in share[1]
    assert phone.taps == [(150, 2000), (980, 2100)]             # mode toggle, then Send
    assert not phone.clip                                        # no paste fallback
    for f in ("01-shared.json", "02-mode.json", "03-sent.json", "04-reply.json"):
        assert isinstance(json.loads((tmp_path / f).read_text()), list)


def test_ask_no_send_stops_before_send(phone):
    phone.screens = [composer_screen(PROMPT)]
    r = chatbots.ask("deepseek", PROMPT, send_it=False, log=lambda m: None)
    assert r == {"app": "deepseek", "mode": "", "sent": False, "reply": ""}
    assert phone.taps == []


def test_ask_confirms_share_dialog(phone):
    ok = screen({"text": "Cancel", "cls": "Button", "click": True, "x": 500, "y": 1260},
                {"text": "Share", "cls": "Button", "click": True, "x": 850, "y": 1260}, pkg=DS)
    phone.screens = [composer_screen(PROMPT), ok, deepseek_reply_screen("391")]
    r = chatbots.ask("deepseek", PROMPT, log=lambda m: None)
    assert phone.taps == [(980, 2100), (850, 1260)]
    assert r["sent"] and r["reply"] == "391"


def test_ask_without_send_button_raises(phone):
    phone.screens = [composer_screen(PROMPT, send=False)]
    with pytest.raises(chatbots.ChatbotError, match="send"):
        chatbots.ask("deepseek", PROMPT, log=lambda m: None)


def test_send_leaves_an_answers_share_button_alone(phone):
    """A fast answer's action row can carry a Share Button (Gemini's makes a public link and
    opens the share sheet). Only a dialog's Share, next to its dismiss button, is consent."""
    answer_row = screen({"text": "391", "cls": "TextView", "x": 300, "y": 900},
                        {"text": "Share", "cls": "Button", "click": True, "x": 400, "y": 1000},
                        {"text": "Copy", "cls": "Button", "click": True, "x": 500, "y": 1000}, pkg=DS)
    phone.screens = [answer_row]
    chatbots.send("deepseek", composer_screen(PROMPT))
    assert phone.taps == [(980, 2100)]
    dialog = screen({"text": "Cancel", "cls": "Button", "click": True, "x": 300, "y": 1200},
                    {"text": "Share", "cls": "Button", "click": True, "x": 700, "y": 1200}, pkg=DS)
    phone.taps.clear()
    phone.screens = [dialog, answer_row]
    chatbots.send("deepseek", composer_screen(PROMPT))
    assert phone.taps == [(980, 2100), (700, 1200)]


def test_wait_times_out_while_generating(phone):
    phone.screens = [composer_screen(PROMPT), deepseek_reply_screen("3", generating=True)]
    r = chatbots.ask("deepseek", PROMPT, wait_s=30, log=lambda m: None)
    assert r["reply"] == "3"
    assert phone.now[0] - 1000.0 < 120          # gave up near the timeout


def test_paste_fallback_when_share_leaves_composer_empty(phone, readable_composer):
    empty = composer_screen("Zpráva DeepSeek")
    paste_menu = screen(*empty, {"text": "Vložit", "click": True, "x": 400, "y": 1950}, pkg=DS)
    phone.screens = [empty] * 11 + [   # 10 s of wait_for reads with the composer empty
        empty,                          # fallback: in app, look for new chat / composer
        empty,                          # composer re-read after clearing
        paste_menu,                     # after long-press
        composer_screen(PROMPT),        # after Paste
        deepseek_reply_screen("391"),   # after Send
    ]
    r = chatbots.ask("deepseek", PROMPT, log=lambda m: None)
    assert phone.clip == [PROMPT]
    assert any(c[0] == "longtap" for c in phone.cmds)
    assert (400, 1950) in phone.taps                # tapped Vložit
    assert phone.taps[-1] == (980, 2100)            # then Send
    assert r["sent"] and r["reply"] == "391"


def test_paste_text_entry_skips_share(phone, monkeypatch):
    monkeypatch.setitem(chatbots.PROFILES["deepseek"], "text_entry", "paste")
    empty = composer_screen("Zpráva DeepSeek")
    paste_menu = screen(*empty, {"text": "Paste", "click": True, "x": 400, "y": 1950}, pkg=DS)
    phone.screens = [empty, empty, paste_menu, composer_screen(PROMPT)]
    r = chatbots.ask("deepseek", PROMPT, send_it=False, log=lambda m: None)
    assert phone.clip == [PROMPT]
    assert not any("android.intent.action.SEND" in " ".join(c) for c in phone.cmds)
    assert r["sent"] is False


def test_paste_fallback_launches_app_when_not_in_front(phone, readable_composer):
    home = screen({"text": "Home"}, pkg="com.android.launcher3")
    empty = composer_screen("Zpráva DeepSeek")
    paste_menu = screen(*empty, {"text": "Paste", "click": True, "x": 400, "y": 1950}, pkg=DS)
    phone.screens = [home] * 11 + [home, empty, empty, paste_menu, composer_screen(PROMPT)]
    chatbots.ask("deepseek", PROMPT, send_it=False, log=lambda m: None)
    assert any(DS in " ".join(c) and "android.intent.action.SEND" not in " ".join(c)
               for c in phone.cmds[1:])


def test_paste_fallback_fails_without_paste_menu(phone, readable_composer):
    empty = composer_screen("Zpráva DeepSeek")
    phone.screens = [empty]
    with pytest.raises(chatbots.ChatbotError, match="[Pp]aste"):
        chatbots.ask("deepseek", PROMPT, log=lambda m: None)


def test_mode_label_missing_lists_screen(phone):
    phone.screens = [composer_screen(PROMPT, mode_label="Something else")]
    with pytest.raises(chatbots.ChatbotError, match="deepthink"):
        chatbots.ask("deepseek", PROMPT, mode="deepthink", log=lambda m: None)


def test_cli_parses_and_dumps(phone, tmp_path, capsys):
    phone.screens = [composer_screen(PROMPT)]
    code = chatbots.main(["deepseek", PROMPT, "--no-send", "--dump", str(tmp_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["app"] == "deepseek" and out["sent"] is False
    assert (tmp_path / "01-shared.json").exists()
    assert chatbots.main(["nosuch", "hi"]) == 1


# ------------------------------------------------------------------ deepseek (real dumps)
# fixtures/chatbots/deepseek/01-new-chat-shared.json: DeepSeek 2.9.0 right after the share
# intent, new chat, prompt in the composer. The composer itself is NOT in the uiautomator tree.
def test_deepseek_real_composer_is_hidden_and_send_is_found():
    ns = load("deepseek/01-new-chat-shared.json")
    assert chatbots.PROFILES["deepseek"]["composer_hidden"] is True
    assert chatbots.in_app("deepseek", ns)
    assert chatbots.composer("deepseek", ns) is None
    assert chatbots._ready("deepseek", ns, PROMPT)
    b = gemini.find(ns, chatbots.PROFILES["deepseek"]["send_labels"], exact=True)
    assert gemini.clickable_at(ns, b) == (988, 1390)               # View#chat_send
    nc = gemini.find(ns, chatbots.PROFILES["deepseek"]["new_chat_labels"])
    assert gemini.clickable_at(ns, nc) == (1029, 172)              # View#chat_new
    assert not chatbots.is_generating("deepseek", ns)


def test_deepseek_real_mode_toggles_and_chrome():
    ns = load("deepseek/01-new-chat-shared.json")
    p = chatbots.PROFILES["deepseek"]
    t = gemini.find(ns, p["modes"]["deepthink"])
    assert gemini.clickable_at(ns, t) == (131, 1375)
    s = gemini.find(ns, p["modes"]["search"])
    assert gemini.clickable_at(ns, s) == (329, 1375)
    assert chatbots._mode_state("deepseek", "deepthink", ns) is None   # droid drops `checked`
    assert chatbots.chat_text("deepseek", ns) == ""                    # greeting, toggles = UI


def test_deepseek_real_share_then_send(phone):
    ns = load("deepseek/01-new-chat-shared.json")
    phone.screens = [ns, deepseek_reply_screen("391")]     # after share, after Send
    r = chatbots.ask("deepseek", PROMPT, log=lambda m: None)
    assert phone.taps == [(988, 1390)]
    assert not phone.clip
    assert r["sent"] and r["reply"] == "391"


def test_deepseek_hidden_composer_without_send_button_raises(phone):
    phone.screens = [screen({"text": "Home"}, pkg="com.android.launcher3")]
    with pytest.raises(chatbots.ChatbotError, match="send button"):
        chatbots.ask("deepseek", PROMPT, log=lambda m: None)
    assert not phone.clip and not phone.taps                   # no paste attempt


# ------------------------------------------------------------------ grok (real dumps)
# fixtures/chatbots/grok/: Grok 1.2.46, one real review request (prompt echo trimmed to 400
# characters). 01 = new chat right after the share intent, 02 = right after Send (the chat
# stays on the tall prompt echo, "Scroll to bottom" shown), 03-05 = the answer scrolled
# through top, middle and end (end: "16 pages" chip, icon buttons, follow-up suggestions).
GROK = chatbots.PROFILES["grok"]


def grok(name):
    return load(f"grok/{name}.json")


GROK_PROMPT = chatbots.composer("grok", grok("01-new-chat-shared"))["text"]


def test_grok_real_share_fills_composer_and_send_is_found():
    ns = grok("01-new-chat-shared")
    c = chatbots.composer("grok", ns)
    assert c["id"] == "chat_text_input" and GROK_PROMPT.startswith("Review this pull request")
    assert chatbots._ready("grok", ns, GROK_PROMPT)
    assert GROK["composer_hidden"] is False and GROK["text_entry"] == "share"
    b = gemini.find(ns, GROK["send_labels"], exact=True)
    assert b["desc"] == "Send message"
    assert gemini.clickable_at(ns, b) == (995, 1395)               # View#input_send_button
    assert not chatbots.is_generating("grok", ns)
    assert chatbots.chat_text("grok", ns) == ""         # Try Premium, Fast, tabs = chrome
    # on the new-chat screen the header button is "Private Chat", not a new-chat button
    assert gemini.find(chatbots._own("grok", ns), GROK["new_chat_labels"]) is None


def test_grok_real_has_no_mode_toggles(phone):
    """1.2.46 has a model chip (Fast) whose popup is not in the tree; Think/DeepSearch are gone."""
    ns = grok("01-new-chat-shared")
    assert chatbots._mode_state("grok", "think", ns) is None
    phone.screens = [ns]
    with pytest.raises(chatbots.ChatbotError, match="no think toggle"):
        chatbots.ask("grok", GROK_PROMPT, mode="think", send_it=False, log=lambda m: None)
    assert phone.taps == []


def test_grok_real_sent_screen_is_only_the_echo():
    ns = grok("02-sent-echo")
    assert chatbots.composer("grok", ns)["text"] == ""              # composer emptied: sent
    assert gemini.find(ns, GROK["send_labels"], exact=True) is None     # Speak instead
    assert not chatbots.is_generating("grok", ns)
    # the echo is ONE multi-line TextView; nothing after it is the answer yet
    assert "\n" in GROK_PROMPT
    assert chatbots.reply_text("grok", ns, GROK_PROMPT) == ""
    j = gemini.find(chatbots._own("grok", ns), GROK["jump_labels"], exact=True)
    assert gemini.clickable_at(ns, j) == (990, 1935)
    nc = gemini.find(chatbots._own("grok", ns), GROK["new_chat_labels"])
    assert gemini.clickable_at(ns, nc) == (990, 197)                 # "Start new chat"


def test_grok_real_reply_top_strips_thought_chip_and_joins_bullets():
    r = chatbots.reply_text("grok", grok("03-reply-top"), GROK_PROMPT)
    lines = r.split("\n")
    assert lines[0] == "MCP protocol correctness (2024-11-05 stdio JSON-RPC):"
    assert "Thought for 2s" not in r and "Review this pull request" not in r
    assert "• tools/list hardcodes tools (no dynamic fetch from TOOLS dict)." in lines
    assert "•" not in lines                                          # no lone bullet nodes
    assert lines[-1] == "Fix: add Pydantic models or jsonschema validation."
    assert "Fast" not in lines and "Ask anything (AI Agent)" not in r


def test_grok_real_reply_end_stops_at_action_buttons():
    ns = grok("05-reply-end")
    r = chatbots.reply_text("grok", ns, GROK_PROMPT)
    assert r.split("\n")[-1].startswith("Overall: Functional but production-unready")
    assert "16 pages" not in r
    assert "Explore MCP security best practices" not in r              # follow-up suggestions
    assert "• mcp_server.py:211: if not self.token: del headers[\"Authorization\"]." in r
    assert chatbots.chat_text("grok", ns) == r                       # echo scrolled away
    assert not chatbots.is_generating("grok", ns)


def test_grok_real_reply_middle():
    r = chatbots.reply_text("grok", grok("04-reply-middle"), GROK_PROMPT)
    assert r.startswith("• Data exposure: Critical.")
    assert "Security of exposed MCP tools (SCAIPOT proxy):\nHigh risk." in r


def test_grok_chrome_re():
    pat = __import__("re").compile(GROK["chrome_re"])
    for t in ("thought for 2s", "thought for 1m 5s", "16 pages", "3 sources"):
        assert pat.fullmatch(t), t
    for t in ("16 pages of results were read", "thought for a while about it"):
        assert not pat.fullmatch(t), t


def test_grok_is_generating_with_stop_button():
    ns = grok("02-sent-echo") + screen(
        {"desc": "Stop", "cls": "View", "click": True, "x": 995, "y": 2200}, pkg=GROK["pkg"])
    assert chatbots.is_generating("grok", ns)


def test_grok_real_share_send_wait_jumps_to_bottom(phone):
    phone.screens = [
        grok("01-new-chat-shared"),   # share intent opened a new chat with the prompt
        grok("02-sent-echo"),         # after Send: prompt echo, Scroll to bottom
        grok("02-sent-echo"),         # wait loop: still on the echo -> tap Scroll to bottom
        grok("05-reply-end"),
        grok("05-reply-end"),
        grok("05-reply-end"),         # unchanged, nothing generating -> done
    ]
    r = chatbots.ask("x", GROK_PROMPT, wait_s=120, log=lambda m: None)
    share = phone.cmds[0]
    assert "ai.x.grok/.main.GrokActivity" in share[1] and "android.intent.action.SEND" in share[1]
    assert phone.taps[0] == (995, 1395)                              # Send
    assert (990, 1935) in phone.taps                                 # Scroll to bottom
    assert not phone.clip                                            # no paste fallback
    assert r["sent"] and r["reply"].endswith("Security exposure is the main blocker.")


def test_grok_second_turn_reply_after_earlier_answers_end_markers():
    """An earlier answer's Copy/Regenerate icons must not cut off the newest reply."""
    ns = screen(
        {"text": "First question", "y": 400},
        {"text": "First answer", "y": 500},
        {"desc": "Copy message", "click": True, "y": 600},
        {"text": "Explore something", "y": 700},                    # earlier suggestion
        {"text": "Second question\nwith two lines", "y": 900},
        {"text": "Thought for 3s", "y": 1000},
        {"text": "•", "x": 54, "y": 1100},
        {"text": "Second answer", "y": 1100},
        {"desc": "Copy message", "click": True, "y": 1300},
        {"text": "Follow-up idea", "y": 1400},
        pkg=GROK["pkg"])
    assert chatbots.reply_text("grok", ns, "Second question\nwith two lines") == "• Second answer"
    assert chatbots.chat_text("grok", ns) == "First question\nFirst answer"


# ------------------------------------------------------------------ chatgpt (real dumps)
# fixtures/chatbots/chatgpt/: ChatGPT 1.2026.321, one real review request (prompt echo and
# composer text trimmed to 400 characters, the pasted answer to 1500; drawer nodes dropped
# from the landscape dumps). 01 = new chat right after the share intent (portrait), 02 = right
# after Send (Stop, first progress line), 03 = mid-answer (Stop, more progress lines), 04 = done,
# top of the chat ("Worked for 45s"; the answer itself is NOT in the tree), 05 = done, end of
# the answer in landscape (Copy / Read aloud / Share / More actions), 06 = the answer pasted
# into the composer after tapping Copy.
CG = chatbots.PROFILES["chatgpt"]


def cg(name):
    return load(f"chatgpt/{name}.json")


CG_PROMPT = chatbots.composer("chatgpt", cg("01-new-chat-shared"))["text"]


def test_chatgpt_real_share_opens_new_chat_with_the_prompt():
    ns = cg("01-new-chat-shared")
    assert CG["share"] == "com.openai.chatgpt/.MainActivity"
    assert CG_PROMPT.startswith("Review this pull request: bugs, async/concurrency issues")
    assert chatbots._ready("chatgpt", ns, CG_PROMPT)
    assert CG["composer_hidden"] is False and CG["text_entry"] == "share"
    b = gemini.find(ns, CG["send_labels"], exact=True)
    assert b["desc"] == "Send Message"
    assert gemini.clickable_at(ns, b) == (985, 1385)
    assert not chatbots.is_generating("chatgpt", ns)
    assert chatbots.chat_text("chatgpt", ns) == ""        # Rejoin Plus, Temporary chat = UI
    assert gemini.find(chatbots._own("chatgpt", ns), CG["new_chat_labels"]) is None
    assert chatbots.copy_button("chatgpt", ns) is None


def test_chatgpt_real_modes_are_not_in_the_tree(phone):
    """No Search toggle; Thinking / Deep research sit in popups uiautomator does not see."""
    ns = cg("01-new-chat-shared")
    assert CG["mode_menu_labels"] == []           # never open the "+" popup blindly
    for key in ("search", "thinking", "deep_research"):
        assert chatbots._mode_state("chatgpt", key, ns) is None
    phone.screens = [ns]
    with pytest.raises(chatbots.ChatbotError, match="no search toggle"):
        chatbots.ask("chatgpt", CG_PROMPT, mode="search", send_it=False, log=lambda m: None)
    assert phone.taps == []


@pytest.mark.parametrize("name", ["02-sent-stop", "03-working-stop"])
def test_chatgpt_real_stop_while_answering(name):
    ns = cg(name)
    assert chatbots.is_generating("chatgpt", ns)
    assert chatbots.composer("chatgpt", ns)["text"] == ""          # sent
    assert chatbots.copy_button("chatgpt", ns) is None
    nc = gemini.find(chatbots._own("chatgpt", ns), CG["new_chat_labels"])
    assert gemini.clickable_at(ns, nc) == (873, 202)


def test_chatgpt_real_done_screen_has_no_answer_text():
    ns = cg("04-done-top")
    assert not chatbots.is_generating("chatgpt", ns)
    assert chatbots.copy_button("chatgpt", ns) is None             # action row is further down
    text = chatbots.chat_text("chatgpt", ns)
    assert "Worked for" not in text and "Show more" not in text
    assert "PR #1 review" not in text            # the answer is drawn outside the tree


def test_chatgpt_real_end_shows_copy_button():
    ns = cg("05-done-end-landscape")
    assert not chatbots.is_generating("chatgpt", ns)
    b = chatbots.copy_button("chatgpt", ns)
    assert b["desc"] == "Copy" and gemini.clickable_at(ns, b) == (1090, 705)
    assert chatbots.composer("chatgpt", ns)["text"] == ""
    assert chatbots.chat_text("chatgpt", ns) == ""


def test_chatgpt_copy_reply_pastes_reads_and_clears(phone):
    phone.screens = [cg("06-copied-in-composer-landscape"), cg("05-done-end-landscape")]
    r = chatbots.copy_reply("chatgpt", cg("05-done-end-landscape"))
    assert r.startswith("### PR #1 review — `sparesparrow/zigbeendicate-sounds`")
    assert "Worked for 45s" not in r
    assert "No authentication; wildcard CORS" in r
    assert phone.taps == [(1090, 705), (1721, 990)]                 # Copy, then composer
    cmds = [c[1] for c in phone.cmds if c[0] == "sh"]
    assert cmds == ["input keyevent 279", chatbots.CLEAR_KEYS]


def test_chatgpt_copy_reply_never_overwrites_a_draft(phone):
    ns = [dict(n, text="my draft") if n.get("edit") else n for n in cg("05-done-end-landscape")]
    with pytest.raises(chatbots.ChatbotError, match="draft"):
        chatbots.copy_reply("chatgpt", ns)
    assert phone.taps == [] and phone.cmds == []


def test_chatgpt_copy_reply_raises_when_composer_stays_full(phone):
    phone.screens = [cg("06-copied-in-composer-landscape")]
    with pytest.raises(chatbots.ChatbotError, match="could not clear"):
        chatbots.copy_reply("chatgpt", cg("05-done-end-landscape"))


def test_chatgpt_real_share_send_wait_copy(phone):
    phone.screens = [
        cg("01-new-chat-shared"),          # share intent opened a new chat with the prompt
        cg("02-sent-stop"),                # after Send
        gemini.GeminiError("droid ui failed"),   # dump fails while the answer streams
        cg("03-working-stop"),
        cg("04-done-top"),                 # done, Copy not on screen -> fling to the end
        cg("05-done-end-landscape"),       # Copy shown -> done
        cg("06-copied-in-composer-landscape"),   # after Copy + Paste
        cg("05-done-end-landscape"),       # composer cleared
    ]
    r = chatbots.ask("ChatGPT", CG_PROMPT, wait_s=150, log=lambda m: None)
    share = phone.cmds[0]
    assert "com.openai.chatgpt/.MainActivity" in share[1] and "android.intent.action.SEND" in share[1]
    assert phone.taps == [(985, 1385), (1090, 705), (1721, 990)]   # Send, Copy, composer
    assert any(c[0] == "sh" and c[1].startswith("input swipe 600 1940 600 420") for c in phone.cmds)
    assert not phone.clip
    assert r["sent"] and r["reply"].startswith("### PR #1 review")


def test_wait_reply_gives_up_when_every_dump_fails(phone):
    phone.screens = [gemini.GeminiError("droid ui failed")]
    with pytest.raises(gemini.GeminiError):
        chatbots.wait_reply("chatgpt", CG_PROMPT, 20)
    assert phone.now[0] - 1000.0 < 40


def test_strip_chrome_head():
    assert chatbots._strip_chrome_head("chatgpt", "Worked for 1m 3s\n\nThinking\n\nAnswer\nmore") \
        == "Answer\nmore"
    assert chatbots._strip_chrome_head("chatgpt", "Answer: worked for 3s") == "Answer: worked for 3s"
    assert chatbots._strip_chrome_head("grok", "Thought for 2s\nHi") == "Hi"


# ------------------------------------------------------------------ perplexity (real dumps)
# fixtures/chatbots/perplexity/: Perplexity 2.105.0, landscape (the phone auto-rotated), one real
# review request (prompt texts trimmed to 400 characters; the home screen's thread history
# dropped). 01 = home, prompt pasted, keyboard up (only composer + Send in the tree); 02 = the
# same with the keyboard hidden ("+" mode menu, Computer toggle, model chip); 03 = the "+"
# Options sheet (Modes: Deep research, Model council, Computer); 04 = the thread right after
# sending (the prompt echo fills the screen, "This question looks difficult" upsell card);
# 05 = flung to the end (follow-up suggestions only); 06-up-01..10 = dragged back up a part of a
# screen at a time until the echo; 07-sources-tab-1..5 = the Sources tab scrolled through.
PX = chatbots.PROFILES["perplexity"]


def px(name):
    return load(f"perplexity/{name}.json")


PX_PROMPT = chatbots.composer("perplexity", px("01-pasted-keyboard-up"))["text"]
PX_UP = [px(f"06-up-{k:02d}") for k in range(1, 11)]
PX_SOURCES = [px(f"07-sources-tab-{k}") for k in range(1, 6)]
PX_ANSWER_FIRST = ("Short answer: I agree. Nothing in the repo's code is affected, because no "
                   "file imports marked.")
PX_ANSWER_LAST = "Set securityLevel: 'strict' explicitly (I haven't verified its default) and " \
                 "consider DOMPurify on the SVG."
PX_ALL_SOURCES = [
    "marked.js: Using Advanced - Marked Documentation",
    "github: Releases · markedjs/marked",
    "github: fix(respec2html): update Renderer for marked v16 token-based API by marcoscaceres · "
    "Pull Request #5140 · speced/respec",
    "github: chore(x-markdown-deps): bump marked from 15.x to 16.x by waiter · Pull Request #1969 "
    "· ant-design/x",
    "nvd.nist: NVD-CVE-2022-21680 - NIST",
    "github: Exponential catastrophic backtracking (ReDoS) in `inline ... - GitHub",
    "github: Security Advisories · markedjs/marked",
    "security.access.redhat: rhsa-2026_26214.json",
    "github: README.md - vmfunc/marked-ReDoS-PoC",
    "oss-security.burmaster: marked (npm) Security Audit — Advisories Mapped",
    "github: Underscore ReDoS · Advisory · markedjs/marked",
    "github: Security",
    "advisories.gitlab: Marked allows Regular Expression Denial of Service (ReDoS) attacks",
    "safeguard: CVE-2022-21680: Marked v4 ReDoS Vulnerability Explained",
    "github: Releases · markedjs/marked-mangle",
]


def px_home_empty():
    return [dict(n, text="") if n.get("edit") else n for n in px("02-pasted-keyboard-hidden")]


def test_perplexity_profile_pastes_because_sharing_submits():
    """Sharing to MainActivity runs the search at once, so --no-send could not hold: paste."""
    assert PX["text_entry"] == "paste" and PX["paste_via"] == "key"
    assert PX["reply_via"] == "scroll" and PX["hide_keyboard"] is True


def test_perplexity_pro_is_absent_and_the_menu_is_closed_again(phone):
    """2.105 has no Pro toggle: the "+" sheet is opened, Pro is not in it, the sheet is closed
    with Back and the error lists what was shown."""
    assert chatbots.resolve_mode("perplexity", "Pro") == "pro"
    phone.screens = [px("03-options-sheet")]
    with pytest.raises(chatbots.ChatbotError, match="no pro toggle"):
        chatbots.pick_mode("perplexity", "pro", px("02-pasted-keyboard-hidden"),
                           log=lambda m: None)
    assert phone.taps == [(437, 970)]                       # only "+"
    assert phone.cmds[-1] == ("key", "back")


def test_perplexity_real_pasted_composer_and_send():
    ns = px("01-pasted-keyboard-up")
    c = chatbots.composer("perplexity", ns)
    assert c["id"] == "input-search" and PX_PROMPT.startswith("Review this dependency upgrade")
    assert chatbots._ready("perplexity", ns, PX_PROMPT)
    b = gemini.find(chatbots._own("perplexity", ns), PX["send_labels"], exact=True)
    assert gemini.clickable_at(ns, b) == (1967, 352)                # View#send-button
    assert not chatbots.is_generating("perplexity", ns)
    hidden = px("02-pasted-keyboard-hidden")
    b = gemini.find(chatbots._own("perplexity", hidden), PX["send_labels"], exact=True)
    assert gemini.clickable_at(hidden, b) == (1967, 970)
    assert chatbots._holds(chatbots.composer("perplexity", hidden), PX_PROMPT)


def test_perplexity_real_mode_state():
    ns = px("02-pasted-keyboard-hidden")
    assert chatbots._mode_state("perplexity", "research", ns) is None
    sheet = px("03-options-sheet")
    t = gemini.find(chatbots._own("perplexity", sheet), PX["modes"]["research"])
    assert t["text"] == "Deep research"
    # the row View#search-mode-item-research is centred too far away (x=1200), so the label
    # itself is tapped; it lies inside the row
    assert gemini.clickable_at(sheet, t) == (494, 636)


def test_perplexity_pick_research_hides_keyboard_then_uses_sheet(phone):
    phone.screens = [px("02-pasted-keyboard-hidden"),       # after Back: the buttons show
                     px("03-options-sheet"),                # after "+": the Options sheet
                     px("02-pasted-keyboard-hidden")]       # after Deep research
    chatbots.pick_mode("perplexity", "research", px("01-pasted-keyboard-up"), log=lambda m: None)
    assert ("key", "back") in phone.cmds
    assert phone.taps == [(437, 970), (494, 636)]           # "+", then Deep research


def test_perplexity_no_back_when_mode_menu_is_shown(phone):
    phone.screens = [px("03-options-sheet"), px("02-pasted-keyboard-hidden")]
    chatbots.pick_mode("perplexity", "research", px("02-pasted-keyboard-hidden"),
                       log=lambda m: None)
    assert ("key", "back") not in phone.cmds
    assert phone.taps == [(437, 970), (494, 636)]


def test_perplexity_real_thread_top_is_the_echo():
    ns = px("04-thread-top-echo")
    assert chatbots.reply_text("perplexity", ns, PX_PROMPT) == ""
    assert chatbots.chat_text("perplexity", ns).startswith("Review this dependency upgrade")
    assert "looks difficult" not in chatbots.chat_text("perplexity", ns)       # upsell = UI
    assert "Upgrade to Pro" not in chatbots.chat_text("perplexity", ns)
    assert not chatbots._ends_shown("perplexity", ns)
    assert not chatbots.is_generating("perplexity", ns)
    assert chatbots.composer("perplexity", ns) is None              # follow-up box collapsed
    nc = gemini.find(chatbots._own("perplexity", ns), PX["new_chat_labels"])
    assert gemini.clickable_at(ns, nc) == (215, 145)                # View#thread-close-button


def test_perplexity_real_end_markers():
    end = px("05-answer-end")                    # only follow-ups: end_ids, not end_labels
    assert chatbots._ends_shown("perplexity", end)
    assert chatbots.chat_text("perplexity", end) == ""
    row = PX_UP[1]                               # "Prepared using ..." and the icon row
    assert chatbots._ends_shown("perplexity", row)
    text = chatbots.chat_text("perplexity", row)
    assert text.endswith("consider DOMPurify on the SVG.")
    assert "Prepared using" not in text and "15 sources" not in text


def test_perplexity_inline_citation_chips_are_sources_not_text():
    ns = PX_UP[7]
    assert chatbots.sources(ns) == ["marked.js", "github"]
    text = chatbots.chat_text("perplexity", ns)
    assert "github" not in text.split("\n") and "github +1" not in text
    assert "marked.js" not in text.split("\n")


def test_perplexity_source_cards():
    assert chatbots.sources(PX_SOURCES[0]) == PX_ALL_SOURCES[:3]
    # a card cut off at the top (snippet only) is skipped, not read as "snippet: ..."
    assert chatbots.sources(PX_SOURCES[1])[0] == PX_ALL_SOURCES[3]


def test_perplexity_scroll_reply_merges_pages(phone):
    phone.screens = PX_UP[:]                     # the last one (with the echo) sticks
    r = chatbots.scroll_reply("perplexity", PX_PROMPT, px("05-answer-end"))
    lines = r.split("\n")
    assert lines[0].startswith(PX_ANSWER_FIRST) and lines[-1].endswith(PX_ANSWER_LAST)
    assert len(lines) == 22 and len(set(lines)) == 22                # no overlap duplicates
    for h in ("Breaking changes (by major version)", "Impact on this repo",
              "Recommended action", "Security notes"):
        assert h in lines
    assert "Review this dependency" not in r and "github +1" not in r
    assert "Prepared using" not in r and "Show me package.json cleanup steps" not in r
    assert all(l == l.rstrip() for l in lines)              # chip placeholders' spaces gone
    swipes = [c[1] for c in phone.cmds if c[0] == "sh"]
    assert swipes[0].count("input swipe 1201 560 1201 270 80") == 6   # fling to the end
    assert swipes[1] == "input swipe 1201 270 1201 560 700"           # then drag back up
    # the echo's visible part is under the header (y < top) on the last page, so the reading
    # stops when a drag no longer moves the chat: the last page is read twice
    assert phone.reads[0] == 11


def test_merge_above():
    assert chatbots._merge_above(["a", "b", "c"], ["b", "c", "d"]) == ["a", "b", "c", "d"]
    assert chatbots._merge_above(["a"], ["b"]) == ["a", "b"]
    assert chatbots._merge_above(["x", "y"], []) == ["x", "y"]


def test_perplexity_read_sources_tab(phone):
    phone.screens = PX_SOURCES[:]                # the last page sticks: nothing new twice
    out = chatbots.read_sources("perplexity", PX_UP[-1])
    assert out == PX_ALL_SOURCES
    assert phone.taps[0] == (1202, 145)                             # Sources tab
    assert phone.taps[-1] == (1042, 145)                            # back to the answer


def test_perplexity_real_paste_send_wait_scroll(phone):
    home = px_home_empty()
    phone.screens = (
        [home,                                   # login check
         home,                                   # in the app, composer empty
         px("01-pasted-keyboard-up"),            # after KEYCODE_PASTE
         px("04-thread-top-echo"),               # after Send
         px("04-thread-top-echo"),               # wait: answer not reached -> fling
         px("05-answer-end")]                    # follow-ups shown, not generating -> done
        + PX_UP + [PX_UP[-1]]                    # scroll_reply, until the chat stops moving
        + [PX_UP[-1]]                            # screen handed to read_sources
        + PX_SOURCES)
    r = chatbots.ask("pplx", PX_PROMPT, wait_s=150, log=lambda m: None)
    assert r["sent"] and r["app"] == "perplexity" and r["mode"] == ""
    assert r["reply"].startswith(PX_ANSWER_FIRST) and r["reply"].endswith(PX_ANSWER_LAST)
    assert r["sources"] == PX_ALL_SOURCES
    assert phone.clip == [PX_PROMPT]                      # pasted, never shared
    assert not any("android.intent.action.SEND" in " ".join(c) for c in phone.cmds)
    assert ("sh", "input keyevent 279") in phone.cmds
    assert phone.taps[:2] == [(1339, 910), (1967, 352)]   # composer, Send
    assert phone.taps[-2:] == [(1202, 145), (1042, 145)]  # Sources tab, back
