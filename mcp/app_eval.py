#!/usr/bin/env python3
"""Live evaluation of the AI-app drivers (mcp-prompts#142): one real task per app, scored.

  app_eval.py run --pr sparesparrow/android-assistant#23 [--apps deepseek,kimi] [--wait 150] [--out DIR]
  app_eval.py run --pr ... --dry-run        print the brief and the plan, touch nothing

The task is real work: each app reviews the same open pull request (title plus a diff excerpt).
Diffs with secret-like strings or .env/credential files are refused. Per app it records the app
version (dumpsys versionName), seconds to answer, reply length, a failure tag (perception, action,
timing, extraction, gate; the rubric of the mcp-prompts prompt android-app-agent-eval) and false
actions (the brief sent more than once). Each step's UI dump is kept under OUT/<app>/ as material
for new fixtures. Results go to OUT/results.json and OUT/results.md; a failure posts a phone
notification naming the app, the step and the tag. Exit 0 only when every app passed with zero
false actions.

It holds the phone's screen lock for the whole run (unless PHONE_SCREEN_LOCK=held). The replies
are untrusted text: this script only stores them. Posting a review comment is left to the caller,
who adds their own assessment.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import chatbots
import gemini
from gemini import GeminiError, norm
from notebook_export import Redactor

ADB = ["adb", "-s", "127.0.0.1:5555", "shell"]
LOCK = Path(os.environ.get("ASSISTANT_STATE", "/root/phone/state")) / "agent.lock"
OUT = Path(os.environ.get("ASSISTANT_STATE", "/root/phone/state")) / "eval"
PACKAGES = {"gemini": "com.google.android.apps.bard",
            **{a: chatbots.PROFILES[a]["pkg"] for a in chatbots.APPS}}
BRIEF_MAX = 7000          # under the drivers' 8000-character prompt limit
MIN_REPLY = 40            # characters; shorter counts as an extraction failure
SECRET_FILES = re.compile(r"^diff --git a/\S*(\.env\b|credential|secret|\.pem|\.key|id_rsa)", re.M | re.I)
TAGS = [  # first match wins; anything else is perception (the driver did not find what it expected)
    ("gate", re.compile(r"sign.?in|log.?in|permission|consent|captcha|verify|locked", re.I)),
    ("timing", re.compile(r"time.?out|timed out|no reply|still generating|within \d+", re.I)),
    ("action", re.compile(r"\bsend\b|\btap|did not open|not in front|share sheet|paste", re.I)),
]


class SkipPR(Exception):
    """The pull request is not fit to send to an app (secrets, credential files)."""


# ------------------------------------------------------------------ pieces (pure)
def parse_version(dumpsys: str) -> str:
    m = re.search(r"versionName=(\S+)", dumpsys)
    return m.group(1) if m else "?"


ECHO = "the reply includes the brief (echo): the driver read our own message as the answer"


def echoes(reply: str, brief: str) -> bool:
    """The extracted reply contains the start of our own brief (seen with Gemini, 2026-11-27)."""
    probes = list(dict.fromkeys(norm(ln) for ln in brief.splitlines() if len(ln.strip()) >= 40))[:12]
    text = norm(reply or "")
    need = 1 if len(probes) <= 2 else 2      # a review may quote one code line; two is an echo
    return bool(probes) and sum(p in text for p in probes) >= need


def classify(error: str | None, reply: str, brief: str = "") -> str | None:
    if error is None:
        if echoes(reply, brief):
            return "extraction"
        return None if len((reply or "").strip()) >= MIN_REPLY else "extraction"
    return next((tag for tag, rx in TAGS if rx.search(error)), "perception")


def false_actions(ns: list[dict], brief: str) -> int:
    """Times the brief appears in the chat beyond the one send."""
    head = norm(brief)[:40]
    copies = sum(1 for n in ns if head and norm(n.get("text") or "").startswith(head))
    return max(0, copies - 1)


def build_brief(repo: str, pr: int, title: str, diff: str) -> str:
    if SECRET_FILES.search(diff):
        raise SkipPR("the diff touches an .env or credential file")
    changed = "\n".join(ln[1:] for ln in diff.splitlines()     # content lines only: git's index
                        if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))  # hashes look like numbers
    red = Redactor([])
    if red(changed) != changed:
        raise SkipPR("the diff contains secret-like strings: " + ", ".join(k for k, v in red.count.items() if v))
    head = (f"Review this pull request: {repo}#{pr} \"{title}\".\n"
            "Answer in English, in at most 8 bullet points: correctness problems first, then risks "
            "and missing tests. Quote file names. If it looks fine, say so in one line.\n\n")
    room = BRIEF_MAX - len(head) - 80
    body = diff if len(diff) <= room else diff[:room] + f"\n[... diff excerpt: first {room} of {len(diff)} characters]"
    return head + "Diff:\n" + body


def exit_code(results: list[dict]) -> int:
    return 0 if all(r["ok"] and r["false_actions"] == 0 for r in results) else 1


def table(results: list[dict]) -> str:
    rows = ["| app | version | result | seconds | reply chars | false actions | note |",
            "|---|---|---|---|---|---|---|"]
    for r in results:
        res = "ok" if r["ok"] else f"FAIL {r['tag']}"
        note = (r.get("error") or "").replace("|", "/")[:80]
        rows.append(f"| {r['app']} | {r['version']} | {res} | {r['seconds']} | {r['reply_chars']} "
                    f"| {r['false_actions']} | {note} |")
    return "\n".join(rows) + "\n"


# ------------------------------------------------------------------ phone (replaced in tests)
def app_version(pkg: str) -> str:
    p = subprocess.run([*ADB, f"dumpsys package {pkg}"], capture_output=True, text=True, timeout=30)
    return parse_version(p.stdout)


def screen() -> list[dict]:
    try:
        return gemini.nodes()
    except GeminiError:
        return []


def notify(title: str, text: str) -> None:
    q = lambda s: "'" + s.replace("'", "'\\''") + "'"  # noqa: E731
    subprocess.run([*ADB, f"cmd notification post -S bigtext -t {q(title)} app-eval {q(text)}"],
                   capture_output=True, timeout=15)


def ask_app(app: str, brief: str, wait_s: int, dump: Path) -> str:
    if app == "gemini":
        return gemini.ask(brief, wait_s=wait_s, log=lambda m: None)["reply"]
    return chatbots.ask(app, brief, wait_s=wait_s, log=lambda m: None, dump=str(dump))["reply"]


@contextlib.contextmanager
def screen_lock(timeout: int = 600):
    if os.environ.get("PHONE_SCREEN_LOCK") == "held":
        yield
        return
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as f:
        end = time.time() + timeout
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > end:
                    raise SystemExit("phone busy: another agent held the screen lock for 10 minutes")
                time.sleep(5)
        yield


# ------------------------------------------------------------------ run
def run(apps: list[str], brief: str, wait_s: int, out: Path) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for app in apps:
        dump = out / app
        dump.mkdir(exist_ok=True)
        r = {"app": app, "version": app_version(PACKAGES[app]), "ok": False, "tag": None, "step": None,
             "error": None, "seconds": 0, "reply_chars": 0, "false_actions": 0}
        start = time.time()
        reply = ""
        try:
            reply = ask_app(app, brief, wait_s, dump)
        except GeminiError as e:
            r["error"], r["step"] = str(e), "ask"
        r["seconds"] = round(time.time() - start)
        r["reply_chars"] = len(reply)
        r["tag"] = classify(r["error"], reply, brief)
        if r["tag"] == "extraction":
            r["step"] = "read reply"
            r["error"] = r["error"] or (ECHO if echoes(reply, brief) else "reply too short")
        r["ok"] = r["tag"] is None
        r["false_actions"] = false_actions(screen(), brief)
        (dump / "reply.txt").write_text(reply)
        if not r["ok"]:
            notify(f"App eval: {app} failed", f"{r['tag']} at {r['step']}: {r['error']}")
        results.append(r)
    (out / "results.json").write_text(json.dumps({"brief_chars": len(brief), "results": results}, indent=1))
    (out / "results.md").write_text(table(results))
    return results


def fetch_pr(ref: str) -> tuple[str, int, str, str]:
    m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", ref)
    if not m:
        raise SystemExit("--pr must look like owner/repo#123")
    repo, num = m.group(1), int(m.group(2))
    title = subprocess.run(["gh", "pr", "view", str(num), "-R", repo, "--json", "title", "-q", ".title"],
                           capture_output=True, text=True, timeout=60, check=True).stdout.strip()
    diff = subprocess.run(["gh", "pr", "diff", str(num), "-R", repo],
                          capture_output=True, text=True, timeout=60, check=True).stdout
    return repo, num, title, diff


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--pr", required=True, help="owner/repo#N, a public pull request to review")
    ap.add_argument("--apps", default=",".join(PACKAGES), help="comma-separated, default all")
    ap.add_argument("--wait", type=int, default=150, help="seconds to wait for each reply")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    apps = [x.strip() for x in a.apps.split(",") if x.strip()]
    bad = [x for x in apps if x not in PACKAGES]
    if bad:
        raise SystemExit(f"unknown app(s): {', '.join(bad)}; known: {', '.join(PACKAGES)}")
    if a.dry_run:
        print(f"would review {a.pr} with: {', '.join(apps)} (wait {a.wait}s each)")
        return 0
    repo, num, title, diff = fetch_pr(a.pr)
    try:
        brief = build_brief(repo, num, title, diff)
    except SkipPR as e:
        raise SystemExit(f"not sending {a.pr}: {e}")
    out = a.out or OUT / time.strftime("%Y%m%d-%H%M%S")
    with screen_lock():
        results = run(apps, brief, a.wait, out)
    print(table(results) + f"\nresults: {out}")
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
