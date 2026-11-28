"""Live evaluation of the app agents across app updates (mcp-prompts #142).

The fixture tests catch regressions in our code, not changes in the apps. This runs one short real
task per app on the phone and scores it with the rubric of the mcp-prompts prompt
`android-app-agent-eval`. It records each app's version, so a failure can be tied to an update.

    app-eval.sh run [APP ...]       run the suite (all apps by default) under the screen lock
    app-eval.sh check               print installed versions and which changed since the last run
    app-eval.sh watch               every APP_EVAL_INTERVAL s (default 1800): run the apps whose
                                    version changed
    app-eval.sh updated PKG         run the app with this package name

The task is a fresh code to repeat, "Reply with only this code: EVAL-K7Q-4821". A stale or cached
reply cannot pass, scoring needs no judgement, and a pass or a fail is about the driver, not the
model: an arithmetic task in the first live run failed Grok for a wrong product (6624 for 6634)
that the driver had read correctly.

Per app it records:
- **success**: the reply holds the right number;
- **time to answer**: in seconds;
- **extraction completeness**: 1.0 if the reply holds the number, 0.5 if it holds other text, 0 if
  it is empty;
- **false actions**: a step on screen in an app other than the one under test, its share sheet,
  the system UI or the launcher. A step in a messaging, mail or social app is a blocker: anything
  sent to a person counts even if the run passed;
- **failure tag**: the step that failed, tagged perception, action, timing, extraction or gate,
  or service when the app itself declined (capacity, plan or rate limits, login): the app is
  down for us, but the driver is not at fault; or environment when adb or the phone failed.

Each step's UI is saved, trimmed to the fields the drivers use, under
state/app-eval/<run>/<app>/. These dumps can hold chat titles from the app's own drawer, so they
stay in state/ and are never committed as is. Promote a dump to fixtures/ only after reading it,
following `android-app-agent-calibrate`.

Results go to state/app-eval/<run>/results.{json,md}, latest.md and history.jsonl. A failed app
posts a phone notification naming the app, the step and the tag.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get("ASSISTANT_STATE", "/root/phone/state"))
OUT = Path(os.environ.get("APP_EVAL_DIR", str(STATE / "app-eval")))
WAIT_S = int(os.environ.get("APP_EVAL_WAIT", "120"))
INTERVAL = int(os.environ.get("APP_EVAL_INTERVAL", "1800"))
ADB = os.environ.get("ADB_SERIAL", os.environ.get("DROID_SERIAL", "127.0.0.1:5555"))

PACKAGES = {
    "gemini": "com.google.android.apps.bard",
    "deepseek": "com.deepseek.chat",
    "kimi": "com.moonshot.kimichat",
    "grok": "ai.x.grok",
    "chatgpt": "com.openai.chatgpt",
    "perplexity": "ai.perplexity.app.android",
}
APPS = tuple(PACKAGES)

# Packages that may show up during a run without it being a false action.
ALLOWED_EXTRA = {
    "android", "com.android.systemui", "com.android.permissioncontroller",
    "com.google.android.permissioncontroller", "com.google.android.inputmethod.latin",
    "com.google.android.googlequicksearchbox",  # Gemini's host app
    "com.motorola.launcher3", "com.google.android.apps.nexuslauncher", "com.android.launcher3",
}
# Apps that reach another person. Any step on screen in one of these is a blocker.
PERSON_APPS = re.compile(
    r"^(com\.whatsapp|org\.telegram|org\.thoughtcrime\.securesms|com\.google\.android\.apps\.messaging"
    r"|com\.android\.mms|com\.samsung\.android\.messaging|com\.google\.android\.gm|com\.microsoft\.office\.outlook"
    r"|ch\.protonmail|com\.facebook\.|com\.instagram\.|com\.twitter\.|com\.discord|com\.slack"
    r"|com\.google\.android\.dialer|com\.android\.dialer|com\.linkedin)")

# The system share sheet: its targets are people (WhatsApp, Messages contacts), one tap from a
# send. The drivers share by explicit component and never need it, so it is a blocker.
SHARE_SHEETS = {"com.android.intentresolver", "com.google.android.intentresolver"}
# The app answered with a refusal instead of an answer.
REFUSED = re.compile(r"too many people|try again later|rate limit|usage limit|reached your limit|"
                     r"upgrade to|subscription|sign in to continue|log in to continue|server is busy|"
                     r"high demand|unavailable", re.I)

# The tags of mcp-prompts #142. In the taxonomy of `android-app-agent-eval`, gate is "policy",
# service is "auth" and environment is "environment"; "app update" is what the version column shows.
TAGS = ("perception", "action", "timing", "extraction", "gate", "service", "environment")
STEP_FIELDS = ("text", "desc", "id", "cls", "pkg", "click", "edit", "x", "y")


# --- the phone ---------------------------------------------------------------------------------

def _adb_shell(cmd: str, timeout: int = 30) -> str:
    p = subprocess.run(["adb", "-s", ADB, "shell", cmd], capture_output=True, text=True, timeout=timeout)
    return p.stdout


def parse_version(dumpsys: str) -> str:
    """versionName from `dumpsys package <pkg>`; the first one is the installed version."""
    m = re.search(r"versionName=(\S+)", dumpsys)
    return m.group(1) if m else ""


def versions(apps=APPS) -> dict[str, str]:
    return {a: parse_version(_adb_shell(f"dumpsys package {PACKAGES[a]}")) for a in apps}


def foreground() -> str:
    """Package of the focused window, or ''."""
    m = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/", _adb_shell("dumpsys window | grep mCurrentFocus"))
    return m.group(1) if m else ""


LAUNCHERS = ("com.motorola.launcher3", "com.google.android.apps.nexuslauncher", "com.android.launcher3")


def idle_reason(power: str, window: str, fg: str) -> str:
    """'' when the phone is free for an unattended run: awake, unlocked and on the home screen.
    Otherwise why not, so the watcher waits instead of taking the screen from the user."""
    if "mWakefulness=Awake" not in power:
        return "the screen is off"
    if re.search(r"isKeyguardShowing=true|mShowingLockscreen=true|mDreamingLockscreen=true", window):
        return "the phone is locked"
    if fg not in LAUNCHERS:
        return f"{fg or 'an app'} is in the foreground"
    return ""


def phone_idle() -> str:
    power = _adb_shell("dumpsys power | grep -m1 mWakefulness")
    window = _adb_shell("dumpsys window | grep -E 'isKeyguardShowing|mShowingLockscreen|mDreamingLockscreen'")
    return idle_reason(power, window, foreground())


def notify(title: str, text: str) -> None:
    q = lambda s: "'" + s.replace("'", "'\\''") + "'"  # noqa: E731 - adb shell quoting
    tag = "app-eval-" + secrets.token_hex(2)
    _adb_shell(f"cmd notification post -S bigtext -t {q(title[:80])} {tag} {q(text[:1000])}")


# --- scoring -----------------------------------------------------------------------------------

CODE_LETTERS = "ACDEFHJKMNPQRTUVWXY"    # no look-alikes (B/8, G/6, I/1, L, O/0, S/5, Z/2)


def make_task(rng: random.Random | None = None) -> tuple[str, str]:
    rng = rng or random.Random()
    code = "EVAL-" + "".join(rng.choice(CODE_LETTERS) for _ in range(3)) + "-" + str(rng.randint(1000, 9999))
    return f"Reply with only this code: {code}", code


def holds_answer(reply: str, expected: str) -> bool:
    """The code appears in the reply as a whole word; case and markdown around it do not matter."""
    return re.search(rf"(?<![\w-]){re.escape(expected)}(?![\w-])", reply or "", re.I) is not None


def completeness(reply: str, expected: str) -> float:
    if holds_answer(reply, expected):
        return 1.0
    return 0.5 if (reply or "").strip() else 0.0


def trim(ns: list[dict]) -> list[dict]:
    """Keep the fields the drivers read, and drop nodes with nothing to read or tap."""
    out = []
    for n in ns:
        if not (n.get("text") or n.get("desc") or n.get("click") or n.get("edit")):
            continue
        out.append({k: n[k] for k in STEP_FIELDS if k in n and n[k] not in ("", None, False)})
    return out


def step_packages(ns: list[dict]) -> set[str]:
    return {n["pkg"] for n in ns if n.get("pkg")}


def false_actions(app: str, steps: dict[str, list[dict]], final_fg: str) -> tuple[list[str], bool]:
    """(what went wrong, blocker). A step showing an app outside the app under test and the
    allowed system packages is a false action; one showing a person-reaching app is a blocker."""
    own = {PACKAGES[app]}
    found, blocker = [], False
    def reaches_people(pkg: str) -> bool:
        return pkg in SHARE_SHEETS or bool(PERSON_APPS.match(pkg))

    for name, ns in steps.items():
        for pkg in sorted(step_packages(ns) - own - ALLOWED_EXTRA):
            found.append(f"{name}: {pkg} on screen")
            blocker = blocker or reaches_people(pkg)
    if final_fg and final_fg not in own | ALLOWED_EXTRA:
        found.append(f"end: {final_fg} in the foreground")
        blocker = blocker or reaches_people(final_fg)
    return found, blocker


def tag_for(step: str, error: str) -> str:
    """Failure tag from the step that failed and its error text."""
    e = error.lower()
    if step == "gate":
        return "gate"
    if e.startswith("the app declined") or re.search(r"not logged in|login required|sign in", e):
        return "service"
    if re.search(r"droid \w+ failed|device offline|no devices|adb|connection refused|screen is locked", e):
        return "environment"
    if re.search(r"timed? ?out|timeout|still generating|no reply within|did not finish", e):
        return "timing"
    if step in ("read", "reply"):
        return "extraction"
    if re.search(r"not found|no composer|cannot find|could not find|not shown|not in the composer|no .* button", e):
        return "perception"
    if step in ("open", "share"):
        return "perception"
    return "action"


# --- one app -----------------------------------------------------------------------------------

class _Steps:
    """Collects a trimmed UI dump after each driver step, by wrapping the driver's log callback."""

    def __init__(self, nodes_fn, outdir: Path | None):
        self.nodes_fn, self.outdir = nodes_fn, outdir
        self.steps: dict[str, list[dict]] = {}
        self.last = "open"

    def log(self, msg: str) -> None:
        m = str(msg).lower()
        # "sent" is logged after the tap, so a failure after it is in waiting or reading.
        self.last = ("read" if "sent" in m else "mode" if ("mode" in m or "tool" in m) else
                     "read" if ("reply" in m or "answer" in m) else "open" if ("chat" in m or "composer" in m) else self.last)

    def snap(self, name: str) -> None:
        try:
            ns = trim(self.nodes_fn())
        except Exception:  # noqa: BLE001 - a failed dump must not hide the real result
            return
        self.steps[f"{len(self.steps) + 1:02d}-{name}"] = ns
        if self.outdir:
            self.outdir.mkdir(parents=True, exist_ok=True)
            (self.outdir / f"{len(self.steps):02d}-{name}.json").write_text(json.dumps(ns, ensure_ascii=False, indent=0))


def _driver(app: str):
    """(ask(prompt, wait_s, log, dump_dir) -> reply text, nodes()). Imported late so the tests
    need no phone. The chat-app driver writes each step's raw dump into dump_dir; Gemini's does
    not, so for Gemini only the end state is checked."""
    sys.path.insert(0, str(HERE))
    import gemini  # noqa: PLC0415
    if app == "gemini":
        return (lambda p, w, log, d: gemini.ask(p, wait_s=w, log=log)["reply"]), gemini.nodes
    import chatbots  # noqa: PLC0415
    return (lambda p, w, log, d: chatbots.ask(app, p, wait_s=w, log=log, dump=d)["reply"]), gemini.nodes


def _driver_steps(raw: Path, st: "_Steps") -> None:
    """Fold the driver's own per-step dumps (01-shared.json, 03-sent.json, ...) into st, trimmed,
    and remove the raw ones."""
    for f in sorted(raw.glob("*.json")):
        try:
            ns = trim(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
        name = f"{len(st.steps) + 1:02d}-driver-{f.stem.split('-', 1)[-1]}"
        st.steps[name] = ns
        if st.outdir:
            st.outdir.mkdir(parents=True, exist_ok=True)
            (st.outdir / f"{name}.json").write_text(json.dumps(ns, ensure_ascii=False, indent=0))
        f.unlink()


def run_app(app: str, version: str = "", outdir: Path | None = None, driver=None, fg_fn=foreground,
            rng: random.Random | None = None, wait_s: int = WAIT_S) -> dict:
    ask, nodes_fn = driver or _driver(app)
    prompt, expected = make_task(rng)
    st = _Steps(nodes_fn, outdir)
    res = {"app": app, "pkg": PACKAGES[app], "version": version, "prompt": prompt, "expected": expected,
           "success": False, "seconds": 0.0, "completeness": 0.0, "reply": "", "false_actions": [],
           "blocker": False, "failed_step": "", "tag": "", "error": ""}
    raw = Path(tempfile.mkdtemp(prefix="app-eval-"))
    t0 = time.monotonic()
    try:
        reply = ask(prompt, wait_s, st.log, str(raw)) or ""
        res["seconds"] = round(time.monotonic() - t0, 1)
        st.snap("reply")
        res["reply"] = reply[:500]
        res["completeness"] = completeness(reply, expected)
        res["success"] = holds_answer(reply, expected)
        if not res["success"]:
            res["failed_step"] = "read"
            res["error"] = ("no reply" if not reply.strip() else
                            f"the app declined: {reply.strip()[:120]}" if REFUSED.search(reply) else
                            "the reply does not hold the code")
    except Exception as e:  # noqa: BLE001 - every driver error is a scored failure, not a crash
        res["seconds"] = round(time.monotonic() - t0, 1)
        st.snap("error")
        res["failed_step"] = st.last
        res["error"] = f"{type(e).__name__}: {e}"[:300]
    _driver_steps(raw, st)
    shutil.rmtree(raw, ignore_errors=True)
    fa, blocker = false_actions(app, st.steps, fg_fn())
    res["false_actions"], res["blocker"] = fa, blocker
    if fa:  # a false action outranks any other failure of the run
        before = f" (also: {res['failed_step']}: {res['error']})" if res["error"] else ""
        res["success"], res["failed_step"], res["error"] = False, "gate", ("; ".join(fa) + before)[:300]
    if not res["success"]:
        res["tag"] = tag_for(res["failed_step"], res["error"])
    return res


# --- the suite ---------------------------------------------------------------------------------

def table(results: list[dict]) -> str:
    rows = ["| App | Version | Result | Time | Complete | False actions | Failed step / tag |",
            "|---|---|---|---|---|---|---|"]
    for r in results:
        verdict = "BLOCKER" if r["blocker"] else ("pass" if r["success"] else "FAIL")
        fail = f"{r['failed_step']} / {r['tag']}: {r['error'][:80]}" if r["tag"] else ""
        rows.append(f"| {r['app']} | {r['version'] or '?'} | {verdict} | {r['seconds']:.0f} s | "
                    f"{r['completeness']:.1f} | {len(r['false_actions'])} | {fail} |")
    return "\n".join(rows)


def run_suite(apps=APPS, run_id: str | None = None, run_app_fn=run_app, versions_fn=versions,
              notify_fn=notify, out: Path = OUT) -> list[dict]:
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
    rdir = out / run_id
    rdir.mkdir(parents=True, exist_ok=True)
    vers = versions_fn(apps)
    results = []
    for app in apps:
        r = run_app_fn(app, version=vers.get(app, ""), outdir=rdir / app)
        results.append(r)
        if not r["success"]:
            notify_fn(f"App agent eval: {app} {'BLOCKER' if r['blocker'] else 'failed'}",
                      f"{app} {r['version']}: step {r['failed_step']}, tag {r['tag']}. {r['error'][:200]}")
    md = (f"# App agent eval {run_id}\n\n{table(results)}\n\n"
          f"False actions in total: {sum(len(r['false_actions']) for r in results)} (must be 0).\n")
    (rdir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    (rdir / "results.md").write_text(md)
    (out / "latest.md").write_text(md)
    with (out / "history.jsonl").open("a") as f:
        for r in results:
            f.write(json.dumps({"run": run_id, **{k: r[k] for k in (
                "app", "version", "success", "seconds", "completeness", "tag", "blocker")},
                "false_actions": len(r["false_actions"])}, ensure_ascii=False) + "\n")
    known = _load_versions(out)
    known.update({a: v for a, v in vers.items() if v})
    (out / "versions.json").write_text(json.dumps(known, indent=1, sort_keys=True))
    return results


def _load_versions(out: Path = OUT) -> dict[str, str]:
    try:
        return json.loads((out / "versions.json").read_text())
    except (OSError, ValueError):
        return {}


def changed(current: dict[str, str], known: dict[str, str]) -> list[str]:
    """Apps whose installed version differs from the last evaluated one (new apps count too)."""
    return [a for a, v in current.items() if v and known.get(a) != v]


def app_for_pkg(pkg: str) -> str | None:
    return next((a for a, p in PACKAGES.items() if p == pkg), None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("apps", nargs="*", metavar="APP", help="any of: " + ", ".join(APPS))
    sub.add_parser("check")
    c = sub.add_parser("changed")
    c.add_argument("--when-idle", action="store_true", help="print nothing unless the phone is idle")
    u = sub.add_parser("updated")
    u.add_argument("pkg")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        bad = [x for x in a.apps if x not in APPS]
        if bad:
            ap.error(f"unknown app {', '.join(bad)}; choose from {', '.join(APPS)}")
        results = run_suite(tuple(a.apps) or APPS)
        print((OUT / "latest.md").read_text())
        return 0 if all(x["success"] for x in results) else 1
    if a.cmd == "updated":
        app = app_for_pkg(a.pkg)
        if not app:
            print(f"{a.pkg} is not an evaluated app")
            return 0
        results = run_suite((app,))
        print((OUT / "latest.md").read_text())
        return 0 if results[0]["success"] else 1
    cur, known = versions(), _load_versions()
    if a.cmd == "changed":
        apps = changed(cur, known)
        why = phone_idle() if (apps and a.when_idle) else ""
        if why:
            print(f"waiting: {' '.join(apps)} changed, but {why}", file=sys.stderr)
            return 0
        print(" ".join(apps))
        return 0
    for app in APPS:
        mark = "changed" if app in changed(cur, known) else ""
        print(f"{app:11} {cur[app] or 'not installed':24} last evaluated {known.get(app, '-'):24} {mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
