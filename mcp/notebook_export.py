"""Export the phone setup into a Gemini Notebook as sources: the android-control and
android-assistant skills, and every Tasker project/profile/task/scene as a readable digest.

  python notebook_export.py build OUTDIR        write the sources as Markdown files (review them)
  python notebook_export.py push OUTDIR [--notebook URL] [--only NAME...]   add them as sources
  python notebook_export.py links URL... [--notebook URL]                     add links as sources

Secrets never leave the phone: API keys, tokens, JWTs, long random strings, the voice trigger
token, values set into variables named like key/token/password/secret, phone numbers and e-mail
addresses are replaced by [redacted]. `build` prints what it redacted (counts, not values).
Tasker's autobackup.xml is read over adb with su; it is not valid XML (attributes without a
space between them) and is repaired before parsing.
"""
from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SKILLS = {
    "android-control": Path("/root/.claude/skills/android-control"),
    "android-assistant": Path("/root/cliphist-android/assistant/skill/android-assistant"),
}
TASKER_BACKUP = "/data/data/net.dinglisch.android.taskerm/files/autobackup.xml"
SERIAL = "127.0.0.1:5555"
VOICE_TOKEN_FILE = Path("/root/voice-assistant/state/http-token")
CHUNK = 70_000          # characters per source

# Tasker action codes whose names are certain (from this phone's exports and the builders);
# anything else is shown as "action #<code>" rather than guessed.
ACTIONS = {
    "20": "Launch App", "30": "Wait", "37": "If", "38": "End If", "39": "For", "40": "End For",
    "43": "Else", "47": "Show Scene", "49": "Destroy Scene", "105": "Set Clipboard",
    "126": "Return", "129": "JavaScriptlet", "130": "Perform Task", "135": "Goto",
    "137": "Stop", "300": "Anchor", "339": "HTTP Request", "360": "Input Dialog",
    "378": "List Dialog", "523": "Notify", "547": "Variable Set", "548": "Flash",
    "549": "Variable Clear", "559": "Say", "590": "Variable Split",
    "598": "Variable Search Replace", "877": "Send Intent",
}
EVENTS = {"201": "Assistance Request"}

SECRET_PATTERNS = [
    ("api key", re.compile(r"\b(?:sk|pk|rk)[-_][A-Za-z0-9_-]{16,}")),
    ("google key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}")),
    ("long random string", re.compile(r"(?<![A-Za-z0-9/_.%-])(?=[A-Za-z0-9+/_-]*\d)(?=[A-Za-z0-9+/_-]*[A-Za-z])[A-Za-z0-9+/_-]{32,}={0,2}")),
    ("e-mail", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phone number", re.compile(r"(?<![\w%])\+?\d[\d -]{7,}\d(?!\w)")),
]
SECRET_VAR = re.compile(r"(key|token|pass|secret|pwd|psk|auth|cookie|session)", re.I)


class Redactor:
    def __init__(self, literals: list[str]):
        self.literals = [s for s in literals if s and len(s) >= 8]
        self.count = collections.Counter()

    def __call__(self, text: str) -> str:
        for lit in self.literals:
            if lit in text:
                self.count["known token"] += text.count(lit)
                text = text.replace(lit, "[redacted]")
        for name, rx in SECRET_PATTERNS:
            text, n = rx.subn("[redacted]", text)
            self.count[name] += n
        return text


# ------------------------------------------------------------------ skills
def skill_source(name: str, root: Path) -> str:
    parts = [f"# Claude Code skill: {name}\n\nExported from the phone's Claude Code setup. "
             f"Files of the skill follow, each under its path.\n"]
    files = [root / "SKILL.md"] + sorted(p for p in root.rglob("*")
                                         if p.is_file() and p.name != "SKILL.md"
                                         and "__pycache__" not in p.parts)
    for f in files:
        rel = f.relative_to(root)
        try:
            body = f.read_text()
        except UnicodeDecodeError:
            continue
        lang = "python" if body.startswith("#!/usr/bin/env python") else ""
        fence = "" if f.suffix == ".md" else f"```{lang}\n"
        parts.append(f"\n## File: {rel}\n\n{fence}{body.rstrip()}\n{'```' if fence else ''}\n")
    return "".join(parts)


# ------------------------------------------------------------------ tasker
def read_tasker() -> ET.Element:
    p = subprocess.run(["adb", "-s", SERIAL, "shell", f"su -c 'cat {TASKER_BACKUP}'"],
                       capture_output=True, timeout=60)
    raw = p.stdout.decode("utf-8", errors="replace")
    if not raw.startswith("<TaskerData"):
        sys.exit(f"could not read Tasker's config: {p.stderr.decode(errors='replace')[:200]}")
    return ET.fromstring(re.sub(r'"(?=[A-Za-z_][\w-]*=")', '" ', raw))


def args_of(el: ET.Element, red: Redactor, limit: int = 1500) -> list[str]:
    out = []
    for a in el:
        if a.tag == "Str" and (a.text or "").strip():
            out.append(f'{a.get("sr")}="{red(a.text.strip())[:limit]}"')
        elif a.tag == "Int" and a.get("val") not in (None, "0"):
            out.append(f'{a.get("sr")}={a.get("val") or (a.findtext("var") or "")}')
        elif a.tag == "Bundle":
            vals = [(v.text or "").strip() for v in a.iter()
                    if v.text and v.text.strip() and not v.tag.endswith(("-type", "KEYS"))
                    and "RELEVANT_VARIABLES" not in v.tag and "RELEVANT_VARIABLES" not in v.text
                    and not v.text.strip().startswith("<") and v.text.strip() != "<null>"]
            if vals:
                out.append("plugin{" + red("; ".join(vals))[:limit] + "}")
    return out


def action_line(a: ET.Element, red: Redactor) -> str:
    code = a.findtext("code") or "?"
    name = ACTIONS.get(code, f"action #{code}")
    args = args_of(a, red, 3000 if code == "129" else 600)
    if code == "547":   # Variable Set: hide values of secret-looking variables
        var = (a.find("Str[@sr='arg0']").text or "") if a.find("Str[@sr='arg0']") is not None else ""
        val_el = a.find("Str[@sr='arg1']")
        if SECRET_VAR.search(var) and val_el is not None and val_el.text and not val_el.text.startswith("%"):
            red.count["secret variable value"] += 1
            args = [f'arg0="{var}"', 'arg1="[redacted]"']
    cond = ""
    cl = a.find("ConditionList")
    if cl is not None:
        cs = [f"{c.findtext('lhs')} op{c.findtext('op')} {red(c.findtext('rhs') or '')}" for c in cl.findall("Condition")]
        cond = "  IF " + " AND ".join(cs)
    label = a.findtext("label")
    return (f"{name}" + (f" [{label}]" if label else "") + (": " + "; ".join(args) if args else "") + cond)


def tasker_digest(root: ET.Element, red: Redactor) -> dict[str, str]:
    tasks = {t.findtext("id"): t for t in root.findall("Task")}
    profiles = {p.findtext("id"): p for p in root.findall("Profile")}
    scenes = {s.findtext("nme"): s for s in root.findall("Scene")}
    used_t, used_p, used_s = set(), set(), set()
    out = {}

    def task_md(tid: str) -> str:
        t = tasks[tid]
        used_t.add(tid)
        name = t.findtext("nme") or f"(anonymous task {tid})"
        acts = sorted(t.findall("Action"), key=lambda a: int(a.get("sr", "act0")[3:] or 0))
        lines = [f"{i + 1}. {action_line(a, red)}" for i, a in enumerate(acts)]
        return f"\n#### Task: {name} (id {tid})\n" + "\n".join(lines) + "\n"

    def profile_md(pid: str) -> str:
        p = profiles[pid]
        used_p.add(pid)
        ctx = []
        for c in p:
            if c.tag in ("Event", "State", "App", "Time", "Day", "Loc"):
                code = c.findtext("code") or ""
                nm = EVENTS.get(code, f"{c.tag.lower()} #{code}" if code else c.tag)
                a = args_of(c, red, 300)
                if c.tag == "App":
                    a = [red(x.text or "") for x in c.iter("label")] or a
                ctx.append(nm + (" (" + "; ".join(a) + ")" if a else ""))
        enter, exit_ = p.findtext("mid0"), p.findtext("mid1")
        s = f"\n### Profile: {p.findtext('nme') or '(unnamed)'} (id {pid})\n- contexts: {', '.join(ctx) or '-'}\n"
        s += f"- enter task: {tasks[enter].findtext('nme') if enter in tasks else enter}\n" if enter else ""
        s += f"- exit task: {tasks[exit_].findtext('nme') if exit_ in tasks else exit_}\n" if exit_ else ""
        return s

    def scene_md(name: str) -> str:
        sc = scenes[name]
        used_s.add(name)
        els = []
        for e in sc:
            if e.tag.endswith("Element") and e.tag != "PropertiesElement":
                txt = [red((x.text or "").strip()) for x in e.findall("Str") if (x.text or "").strip()]
                click = e.findtext("clickTask")
                els.append(f"- {e.tag.replace('Element', '')} {e.findtext('nme') or ''}"
                           + (f": {' | '.join(txt)[:200]}" if txt else "")
                           + (f" -> task {tasks[click].findtext('nme') if click in tasks else click}" if click else ""))
        return f"\n### Scene: {name}\n" + "\n".join(els) + "\n"

    for pr in root.findall("Project"):
        pname = pr.findtext("name")
        md = [f"## Tasker project: {pname}\n"]
        for pid in filter(None, (pr.findtext("pids") or "").split(",")):
            if pid in profiles:
                md.append(profile_md(pid))
        for sn in filter(None, (pr.findtext("scenes") or "").split(",")):
            if sn in scenes:
                md.append(scene_md(sn))
        tids = [t for t in (pr.findtext("tids") or "").split(",") if t in tasks]
        if tids:
            md.append("\n### Tasks\n")
            md += [task_md(t) for t in tids]
        out[pname] = "".join(md)
    rest = ["## Not in a project\n"]
    rest += [profile_md(p) for p in profiles if p not in used_p]
    rest += [scene_md(s) for s in scenes if s not in used_s]
    rest += [task_md(t) for t in tasks if t not in used_t and tasks[t].findtext("nme")]
    out["(no project)"] = "".join(rest)
    return out


def chunk_projects(digest: dict[str, str]) -> list[tuple[str, str]]:
    head = ("# Tasker configuration of the phone ({part})\n\nExported from Tasker's autobackup "
            "(Tasker 6.7). Projects group profiles (contexts that trigger an enter/exit task), "
            "scenes (custom UI) and tasks (numbered actions). Action names are given where the "
            "code is certain, otherwise 'action #code'. Secrets, phone numbers and e-mails are "
            "[redacted].\n")
    out, cur, names = [], "", []
    for name, md in digest.items():
        if len(md) > CHUNK:                       # one huge project: split by tasks
            pieces = re.split(r"(?=\n#### Task: )", md)
            buf = ""
            for pc in pieces:
                if len(buf) + len(pc) > CHUNK and buf:
                    out.append((f"{name} (part)", buf)); buf = ""
                buf += pc
            out.append((name, buf))
            continue
        if len(cur) + len(md) > CHUNK and cur:
            out.append((", ".join(names), cur)); cur, names = "", []
        cur += md + "\n"; names.append(name)
    if cur:
        out.append((", ".join(names), cur))
    return [(t, head.format(part=f"{i + 1}/{len(out)}: {t}"[:200]) + body) for i, (t, body) in enumerate(out)]


# ------------------------------------------------------------------ commands
def build(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    outdir.chmod(0o700)
    literals = []
    try:
        literals.append(VOICE_TOKEN_FILE.read_text().strip())
    except OSError:
        pass
    red = Redactor(literals)
    files = []
    for name, root in SKILLS.items():
        f = outdir / f"10-skill-{name}.md"
        f.write_text(red(skill_source(name, root)))
        files.append(f)
    for i, (title, text) in enumerate(chunk_projects(tasker_digest(read_tasker(), red)), 1):
        f = outdir / f"20-tasker-{i:02d}.md"
        f.write_text(text)
        files.append(f)
    for f in files:
        print(f"{f.name}: {len(f.read_text()):>7} chars  {f.read_text().splitlines()[0][:90]}")
    print("redacted:", dict(red.count) or "nothing")


def push(outdir: Path, notebook: str, only: list[str]) -> None:
    import notebook as nb
    for f in sorted(outdir.glob("*.md")):
        if only and not any(o in f.name for o in only):
            continue
        try:
            r = nb.add_source(f.read_text(), notebook, log=lambda m: None)
            print(f"{f.name}: {'added' if r.get('added') else r}")
        except nb.GeminiError as e:
            print(f"{f.name}: FAILED {e}")


def links(urls: list[str], notebook: str) -> None:
    import notebook as nb
    for u in urls:
        if not re.fullmatch(r"https://[\w.-]+/\S*", u):
            print(f"{u}: not an https link"); continue
        try:
            r = nb.add_source(u, notebook, log=lambda m: None)
            print(f"{u}: {'added' if r.get('added') else r}")
        except nb.GeminiError as e:
            print(f"{u}: FAILED {e}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "push", "links"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--notebook", default="")
    ap.add_argument("--only", nargs="*", default=[])
    a = ap.parse_args()
    if a.cmd == "build":
        build(Path(a.args[0]))
    elif a.cmd == "push":
        push(Path(a.args[0]), a.notebook, a.only)
    else:
        links(a.args, a.notebook)
