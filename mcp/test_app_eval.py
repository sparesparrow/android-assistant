"""The live app-agent eval (app_eval.py, mcp-prompts #142), offline: scoring, failure tags, false
actions, the results files, version changes and the screen lock of agents/app-eval.sh."""
import json
import os
import random
import re
import subprocess
from pathlib import Path

import pytest

import app_eval as ev
import chatbots
import gemini

ROOT = Path(__file__).resolve().parent.parent


def test_packages_match_the_drivers():
    for app in chatbots.APPS:
        assert ev.PACKAGES[app] == chatbots.PROFILES[app]["pkg"]
    assert ev.PACKAGES["gemini"] == gemini.ENTRY.split("/")[0]


def test_parse_version_takes_the_installed_one():
    out = "Packages:\n  Package [ai.x.grok]\n    versionCode=246 minSdk=28\n    versionName=1.2.46-release.00\n" \
          "  Hidden system packages:\n    versionName=1.0.0\n"
    assert ev.parse_version(out) == "1.2.46-release.00"
    assert ev.parse_version("Unable to find package: x") == ""


def test_task_is_a_fresh_code_to_repeat():
    p, code = ev.make_task(random.Random(7))
    assert p == f"Reply with only this code: {code}"
    assert re.fullmatch(r"EVAL-[A-Z]{3}-\d{4}", code) and not set(code[5:8]) & set("BGILOSZ")
    assert ev.make_task(random.Random(8))[1] != code


@pytest.mark.parametrize("reply,ok", [
    ("EVAL-KQX-4821", True), ("**EVAL-KQX-4821**", True), ("Here it is: eval-kqx-4821.", True),
    ("`EVAL-KQX-4821`\nThought for 1 seconds", True), ("EVAL-KQX-48210", False),
    ("EVAL-KQX-482", False), ("XEVAL-KQX-4821", False), ("", False),
])
def test_holds_answer(reply, ok):
    assert ev.holds_answer(reply, "EVAL-KQX-4821") is ok


def test_completeness():
    assert ev.completeness("EVAL-KQX-4821", "EVAL-KQX-4821") == 1.0
    assert ev.completeness("I can't help with that", "EVAL-KQX-4821") == 0.5
    assert ev.completeness("  ", "EVAL-KQX-4821") == 0.0


def test_trim_keeps_what_the_drivers_read():
    ns = [{"i": 0, "text": "", "desc": "", "id": "", "cls": "View", "pkg": "p", "click": False, "edit": False, "x": 1, "y": 2},
          {"i": 1, "text": "Send", "desc": "", "id": "send", "cls": "Button", "pkg": "p", "click": True, "edit": False, "x": 5, "y": 6}]
    assert ev.trim(ns) == [{"text": "Send", "id": "send", "cls": "Button", "pkg": "p", "click": True, "x": 5, "y": 6}]


def _ns(*pkgs):
    return [{"text": "x", "pkg": p} for p in pkgs]


def test_false_actions():
    own = ev.PACKAGES["deepseek"]
    ok = {"01-reply": _ns(own, "com.android.systemui", "android")}
    assert ev.false_actions("deepseek", ok, own) == ([], False)
    stray = {"01-reply": _ns(own, "com.android.settings")}
    assert ev.false_actions("deepseek", stray, own) == (["01-reply: com.android.settings on screen"], False)
    sheet = {"01-reply": _ns(own, "com.android.intentresolver")}
    assert ev.false_actions("deepseek", sheet, own) == (["01-reply: com.android.intentresolver on screen"], True)
    sent = {"01-error": _ns("com.whatsapp")}
    found, blocker = ev.false_actions("deepseek", sent, "com.whatsapp")
    assert blocker and len(found) == 2


@pytest.mark.parametrize("step,error,tag", [
    ("open", "ChatbotError: composer not found", "perception"),
    ("open", "droid open failed", "environment"),
    ("open", "the Gemini overlay did not open", "perception"),
    ("send", "ChatbotError: no Send button", "perception"),
    ("send", "tap missed", "action"),
    ("mode", "the toggle did not change", "action"),
    ("read", "no reply within 120 s", "timing"),
    ("read", "the reply does not hold the code", "extraction"),
    ("read", "the app declined: Too many people are chatting with Kimi", "service"),
    ("open", "ChatbotError: kimi is not logged in", "service"),
    ("open", "GeminiError: droid ui failed: error: device offline", "environment"),
    ("gate", "01-reply: com.android.settings on screen", "gate"),
])
def test_tag_for(step, error, tag):
    assert ev.tag_for(step, error) == tag


def _driver(reply=None, error=None, pkgs=("com.deepseek.chat",), logs=("new chat, prompt in composer", "sent")):
    def ask(prompt, wait_s, log, dump):
        if dump:
            Path(dump, "01-shared.json").write_text(json.dumps([{"text": "composer", "pkg": pkgs[0], "x": 1}]))
        for m in logs:
            log(m)
        if error:
            raise error
        code = prompt.rsplit(" ", 1)[1]
        return reply if reply is not None else code
    return ask, lambda: _ns(*pkgs)


def test_run_app_pass(tmp_path):
    r = ev.run_app("deepseek", "2.9.0", tmp_path / "d", driver=_driver(), fg_fn=lambda: "com.deepseek.chat")
    assert r["success"] and r["completeness"] == 1.0 and r["tag"] == "" and r["false_actions"] == []
    assert sorted(p.name for p in (tmp_path / "d").iterdir()) == ["01-reply.json", "02-driver-shared.json"]


def test_run_app_refusal_is_service():
    r = ev.run_app("kimi", driver=_driver(reply="Too many people are chatting with Kimi; a subscription will grant you priority",
                                   pkgs=("com.moonshot.kimichat",)),
                   fg_fn=lambda: "com.moonshot.kimichat")
    assert not r["success"] and r["tag"] == "service" and r["error"].startswith("the app declined: Too many")


def test_gemini_share_sheet_run_is_a_blocker():
    """The first live run: Gemini's driver tapped the answer's Share button, which ended in the
    system share sheet with contacts listed (android-assistant#24)."""
    r = ev.run_app("gemini", driver=_driver(reply="", pkgs=("com.android.intentresolver",)),
                   fg_fn=lambda: "com.android.intentresolver")
    assert r["blocker"] and r["tag"] == "gate" and r["failed_step"] == "gate"
    assert "intentresolver on screen" in r["error"] and "(also: read: no reply)" in r["error"]


def test_run_app_wrong_reply_is_extraction(tmp_path):
    r = ev.run_app("deepseek", driver=_driver(reply="I think 7"), fg_fn=lambda: "com.deepseek.chat")
    assert not r["success"] and (r["failed_step"], r["tag"], r["completeness"]) == ("read", "extraction", 0.5)


def test_run_app_driver_error_is_tagged_at_its_step():
    r = ev.run_app("deepseek", driver=_driver(error=chatbots.ChatbotError("no Send button"), logs=("new chat",)),
                   fg_fn=lambda: "com.deepseek.chat")
    assert (r["failed_step"], r["tag"]) == ("open", "perception") and "no Send button" in r["error"]
    r = ev.run_app("deepseek", driver=_driver(error=TimeoutError("no reply within 120 s")),
                   fg_fn=lambda: "com.deepseek.chat")
    assert (r["failed_step"], r["tag"]) == ("read", "timing")
    # ChatGPT's live failure (2026-11-27): the copy-and-paste read of its answer came back empty.
    r = ev.run_app("chatgpt", driver=_driver(error=chatbots.ChatbotError("pasting into the chatgpt composer gave no text"),
                                             pkgs=("com.openai.chatgpt",)), fg_fn=lambda: "com.openai.chatgpt")
    assert (r["failed_step"], r["tag"]) == ("read", "extraction")


def test_a_false_action_fails_a_correct_answer():
    r = ev.run_app("deepseek", driver=_driver(pkgs=("com.deepseek.chat", "com.android.settings")),
                   fg_fn=lambda: "com.deepseek.chat")
    assert not r["success"] and r["tag"] == "gate" and not r["blocker"]
    r = ev.run_app("deepseek", driver=_driver(), fg_fn=lambda: "org.telegram.messenger")
    assert r["blocker"] and r["tag"] == "gate"


def test_run_suite_writes_results_and_notifies_failures(tmp_path):
    notes = []

    def fake(app, version, outdir):
        ok = app != "kimi"
        return {"app": app, "pkg": ev.PACKAGES[app], "version": version, "success": ok, "seconds": 12.3,
                "completeness": 1.0 if ok else 0.0, "reply": "", "false_actions": [], "blocker": False,
                "failed_step": "" if ok else "send", "tag": "" if ok else "action", "error": "" if ok else "tap missed"}

    res = ev.run_suite(("deepseek", "kimi"), run_id="r1", run_app_fn=fake,
                       versions_fn=lambda apps: {"deepseek": "2.9.0", "kimi": "3.1.8"},
                       notify_fn=lambda t, x: notes.append((t, x)), out=tmp_path)
    assert [r["success"] for r in res] == [True, False]
    assert notes == [("App agent eval: kimi failed", "kimi 3.1.8: step send, tag action. tap missed")]
    md = (tmp_path / "latest.md").read_text()
    assert "| deepseek | 2.9.0 | pass | 12 s | 1.0 | 0 |" in md and "| kimi | 3.1.8 | FAIL |" in md
    assert "send / action: tap missed" in md and "False actions in total: 0" in md
    assert json.loads((tmp_path / "r1" / "results.json").read_text())[1]["tag"] == "action"
    assert len((tmp_path / "history.jsonl").read_text().splitlines()) == 2
    assert json.loads((tmp_path / "versions.json").read_text()) == {"deepseek": "2.9.0", "kimi": "3.1.8"}


def test_changed_and_pkg_lookup():
    assert ev.changed({"kimi": "3.1.9", "grok": "1.2", "chatgpt": ""}, {"kimi": "3.1.8", "grok": "1.2"}) == ["kimi"]
    assert ev.changed({"gemini": "1.0"}, {}) == ["gemini"]
    assert ev.app_for_pkg("ai.x.grok") == "grok" and ev.app_for_pkg("com.whatsapp") is None


def test_wrapper_reports_a_busy_screen(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    env = {**os.environ, "ASSISTANT_STATE": str(state), "APP_EVAL_LOCK_WAIT": "1"}
    env.pop("PHONE_SCREEN_LOCK", None)
    holder = subprocess.Popen(["flock", str(state / "agent.lock"), "sleep", "5"])
    try:
        import time
        time.sleep(0.3)
        p = subprocess.run([str(ROOT / "agents" / "app-eval.sh"), "run", "deepseek"], env=env,
                           capture_output=True, text=True, timeout=30)
    finally:
        holder.kill()
    assert p.returncode == 75 and "screen is busy" in p.stderr


def test_wrapper_rejects_unknown_apps(tmp_path):
    env = {**os.environ, "ASSISTANT_STATE": str(tmp_path), "PHONE_SCREEN_LOCK": "held"}
    p = subprocess.run([str(ROOT / "agents" / "app-eval.sh"), "run", "whatsapp"], env=env,
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and "unknown app whatsapp" in p.stderr


def test_a_driver_step_in_another_app_is_a_false_action(tmp_path):
    def ask(prompt, wait_s, log, dump):
        Path(dump, "03-sent.json").write_text(json.dumps([{"text": "Chats", "pkg": "com.whatsapp"}]))
        code = prompt.rsplit(" ", 1)[1]
        return code
    r = ev.run_app("kimi", outdir=tmp_path, driver=(ask, lambda: _ns("com.moonshot.kimichat")),
                   fg_fn=lambda: "com.moonshot.kimichat")
    assert r["blocker"] and r["false_actions"] == ["02-driver-sent: com.whatsapp on screen"]


@pytest.mark.parametrize("power,window,fg,why", [
    ("mWakefulness=Awake", "isKeyguardShowing=false", "com.motorola.launcher3", ""),
    ("mWakefulness=Asleep", "", "com.motorola.launcher3", "the screen is off"),
    ("mWakefulness=Awake", "    isKeyguardShowing=true", "com.android.systemui", "the phone is locked"),
    ("mWakefulness=Awake", "isKeyguardShowing=false", "com.termux", "com.termux is in the foreground"),
])
def test_the_watcher_waits_for_an_idle_phone(power, window, fg, why):
    assert ev.idle_reason(power, window, fg) == why
