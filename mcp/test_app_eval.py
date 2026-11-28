"""app_eval.py: the live suite of mcp-prompts#142, tested offline with fake drivers. No phone."""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import app_eval as ev  # noqa: E402
import gemini  # noqa: E402

DIFF = "diff --git a/x.py b/x.py\n+def f():\n+    return 1\n"


def test_parse_version_takes_the_first_version_name():
    out = "  Packages:\n    versionCode=123 minSdk=26\n    versionName=1.2.3\n    versionName=9\n"
    assert ev.parse_version(out) == "1.2.3" and ev.parse_version("nothing") == "?"


@pytest.mark.parametrize("error,reply,tag", [
    (None, "A long enough review of the change with real points.", None),
    (None, "ok", "extraction"),
    ("Sign in to continue", "", "gate"),
    ("no reply within 120 s (still generating)", "", "timing"),
    ("no Send button", "", "action"),
    ("composer not found", "", "perception"),
])
def test_classify(error, reply, tag):
    assert ev.classify(error, reply) == tag


def test_false_actions_counts_duplicate_sends():
    brief = "Review this pull request: sparesparrow/android-assistant#23 and list problems"
    one = [{"text": brief, "pkg": "p"}, {"text": "answer", "pkg": "p"}]
    two = one + [{"text": brief, "pkg": "p"}]
    assert ev.false_actions(one, brief) == 0 and ev.false_actions(two, brief) == 1


def test_brief_fits_the_prompt_limit_and_names_the_pr():
    b = ev.build_brief("sparesparrow/android-assistant", 23, "fix: x", DIFF * 2000)
    assert len(b) <= ev.BRIEF_MAX and "sparesparrow/android-assistant#23" in b and "excerpt" in b


@pytest.mark.parametrize("diff", [DIFF + "+KEY = 'ghp_" + "a" * 36 + "'\n",
                                  "diff --git a/.env b/.env\n-TOKEN=x\n"])
def test_brief_refuses_diffs_with_secrets_or_env_files(diff):
    with pytest.raises(ev.SkipPR):
        ev.build_brief("sparesparrow/x", 1, "t", diff)


def _fake(monkeypatch, replies, versions=None, screens=None):
    calls = []

    def ask(app, brief, wait_s, dump):
        calls.append(app)
        r = replies[app]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(ev, "ask_app", ask)
    monkeypatch.setattr(ev, "app_version", lambda pkg: (versions or {}).get(pkg, "1.0"))
    monkeypatch.setattr(ev, "screen", lambda: screens or [])
    monkeypatch.setattr(ev, "notify", lambda title, text: calls.append(("notify", title, text)))
    return calls


def test_run_scores_each_app_and_notifies_failures(monkeypatch, tmp_path):
    good = "Looks fine overall. One issue: the timeout is not capped. Tests cover the main path."
    calls = _fake(monkeypatch, {"deepseek": good, "kimi": gemini.GeminiError("no Send button")})
    results = ev.run(["deepseek", "kimi"], "brief text", wait_s=60, out=tmp_path)
    by = {r["app"]: r for r in results}
    assert by["deepseek"]["ok"] and by["deepseek"]["tag"] is None and by["deepseek"]["version"] == "1.0"
    assert not by["kimi"]["ok"] and by["kimi"]["tag"] == "action" and by["kimi"]["step"] == "ask"
    assert ("notify", "App eval: kimi failed", "action at ask: no Send button") in calls
    saved = json.loads((tmp_path / "results.json").read_text())
    assert [r["app"] for r in saved["results"]] == ["deepseek", "kimi"]
    md = (tmp_path / "results.md").read_text()
    assert "| deepseek | 1.0 | ok |" in md and "| kimi | 1.0 | FAIL action |" in md


def test_exit_code_is_nonzero_on_any_failure_or_false_action():
    ok = {"ok": True, "false_actions": 0}
    assert ev.exit_code([ok]) == 0
    assert ev.exit_code([ok, {"ok": False, "false_actions": 0}]) == 1
    assert ev.exit_code([{"ok": True, "false_actions": 1}]) == 1


def test_unknown_app_is_refused():
    with pytest.raises(SystemExit):
        ev.main(["run", "--apps", "nonsense", "--pr", "sparesparrow/x#1", "--dry-run"])


def test_brief_ignores_git_metadata_lines():
    diff = "diff --git a/x b/x\nindex 1f2e3d4..3938192 100644\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
    assert "Diff:" in ev.build_brief("sparesparrow/x", 1, "t", diff)


def test_skip_message_names_only_what_matched():
    with pytest.raises(ev.SkipPR) as e:
        ev.build_brief("sparesparrow/x", 1, "t", DIFF + "+KEY = 'ghp_" + "a" * 36 + "'\n")
    assert "github token" in str(e.value) and "phone number" not in str(e.value)


def test_a_reply_that_echoes_the_brief_is_an_extraction_failure(monkeypatch, tmp_path):
    brief = ev.build_brief("sparesparrow/x", 1, "fix: thing", DIFF)
    echoed = brief + "\n\n- one real point about the change, long enough to count"
    assert ev.classify(None, echoed, brief) == "extraction"
    assert ev.classify(None, "- one real point about the change, long enough to count", brief) is None
    _fake(monkeypatch, {"gemini": echoed})
    r = ev.run(["gemini"], brief, wait_s=60, out=tmp_path)[0]
    assert not r["ok"] and r["tag"] == "extraction" and "echo" in r["error"]


def test_a_partial_echo_without_the_first_line_is_caught():
    brief = ev.build_brief("sparesparrow/x", 1, "fix: thing", DIFF * 3)
    partial = brief.split("\n", 1)[1] + "\n\n- the real answer, long enough to count as a reply"
    assert ev.classify(None, partial, brief) == "extraction"


def test_quoting_one_code_line_is_not_an_echo():
    lines = [f"+    value_{i} = compute_something_long(argument_{i})\n" for i in range(6)]
    diff = "diff --git a/x b/x\n" + "".join(lines)
    brief = ev.build_brief("sparesparrow/x", 1, "t", diff)
    review = "- `value_3 = compute_something_long(argument_3)` is never used; the rest looks fine."
    assert ev.classify(None, review, brief) is None
