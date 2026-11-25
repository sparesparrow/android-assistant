#!/usr/bin/env python3
"""Build the Tasker project "Claude Agents": one task per Claude Code agent in
assistant/agents/*.md ("Agent gemini", "Agent phone", ...), as one .prj.xml for Tasker's own
importer (import-claude-agents.sh).

  build-claude-agents.py OUT.prj.xml      real build
  build-claude-agents.py --dry-run         XML to stdout

Each task: Input Dialog (sets %input) -> JavaScriptlet sendIntent() to Termux's
RunCommandService:
  com.termux.RUN_COMMAND_PATH       ~/.termux/tasker/agent-<name>   (installed by install.sh)
  com.termux.RUN_COMMAND_STDIN      the typed prompt (never on a command line)
  com.termux.RUN_COMMAND_BACKGROUND true
Tasker holds com.termux.permission.RUN_COMMAND; Termux needs allow-external-apps = true.
agent-<name> runs termux/claude-agent, which ssh-es into the Debian PRoot when its agent sshd
(127.0.0.1:8023) is up and falls back to proot-distro login. The result comes back as a phone
notification posted by run-agent.sh. No token or secret is in this XML.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("panel", HERE / "build-claude-panel.py")
panel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(panel)
sub, javascriptlet, input_dialog, to_xml = panel.sub, panel.javascriptlet, panel.input_dialog, panel.to_xml

PROJECT = "Claude Agents"
AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"
TERMUX_TASKER = "/data/data/com.termux/files/home/.termux/tasker"
FIRST_TASK_ID = 9301
TIME = "1794700000000"
HINTS = {
    "gemini": ("Ask Gemini", "Question for Gemini. Add +Deep Research, +Create images, +Videos, "
               "+Music, +Canvas or +Guided Learning, or @notebook to ask the shared notebook. "
               "Answer comes as a notification."),
    "deepseek": ("Ask DeepSeek", "Question for DeepSeek. Add +DeepThink or +Search. "
                 "Answer comes as a notification."),
    "kimi": ("Ask Kimi", "Question for Kimi. Add +Thinking or +Search. "
             "Answer comes as a notification."),
    "grok": ("Ask Grok", "Question for Grok. Add +Think or +DeepSearch. "
             "Answer comes as a notification."),
    "chatgpt": ("Ask ChatGPT", "Question for ChatGPT. Add +Search or +Deep Research. "
                "Answer comes as a notification."),
    "perplexity": ("Ask Perplexity", "Question for Perplexity. Add +Pro or +Research. "
                   "Answer comes as a notification."),
    "phone": ("Phone agent","What should Claude do on the phone? Result comes as a notification."),
}


def agents(d: Path = AGENTS_DIR) -> list[str]:
    names = sorted(p.stem for p in d.glob("*.md"))
    bad = [n for n in names if not re.fullmatch(r"[a-z0-9-]+", n)]
    if bad:
        raise ValueError(f"agent names must be [a-z0-9-]: {bad}")
    return names


def js_send(agent: str) -> str:
    path = f"{TERMUX_TASKER}/agent-{agent}"
    return (
        "var p = (typeof input !== 'undefined') ? String(input).trim() : '';\n"
        "if (p && p.indexOf('%input') !== 0) {\n"
        "  sendIntent('com.termux.RUN_COMMAND', 'service', 'com.termux',\n"
        "    'com.termux.app.RunCommandService', 'none', '', '',\n"
        f"    [{json.dumps('com.termux.RUN_COMMAND_PATH:' + path)},\n"
        "     'com.termux.RUN_COMMAND_STDIN:' + p,\n"
        "     'com.termux.RUN_COMMAND_BACKGROUND:true']);\n"
        f"  flash({json.dumps('Claude ' + agent + ': working…')});\n"
        "}\n")


CHOOSER = "Ask AI"   # one task for a Quick Settings tile or widget: pick an agent, then ask it
LABELS = {"gemini": "Gemini", "deepseek": "DeepSeek", "chatgpt": "ChatGPT", "grok": "Grok",
          "perplexity": "Perplexity", "kimi": "Kimi", "phone": "Phone"}


def js_items(names: list[str]) -> str:
    items = ",".join(LABELS.get(n, n) for n in names)
    return f"setLocal('ai_items', {json.dumps(items)});\n"


def js_pick(names: list[str]) -> str:
    table = {LABELS.get(n, n): f"Agent {n}" for n in names}
    return (
        f"var t = {json.dumps(table)}, sel = '';\n"
        "try { sel = String(local('ld_selected') || ''); } catch (e) {}\n"
        "if (t[sel]) performTask(t[sel], 100, '', '');\n")


def build(names: list[str]) -> ET.Element:
    root = ET.Element("TaskerData", sr="", dvi="1", tv="6.7.6-beta")
    ids = {n: FIRST_TASK_ID + i for i, n in enumerate(names)}
    proj = sub(root, "Project", sr="proj0", ve="2")
    sub(proj, "cdate", TIME)
    sub(proj, "id", str(uuid.uuid5(uuid.NAMESPACE_URL, "claude-agents-tasker-project")))
    sub(proj, "name", PROJECT)
    chooser_id = FIRST_TASK_ID + len(names)
    sub(proj, "tids", ",".join(str(i) for i in [*ids.values(), chooser_id]))
    for name, tid in ids.items():
        t = sub(root, "Task", sr=f"task{tid}")
        for tag, val in (("cdate", TIME), ("edate", TIME), ("id", tid), ("nme", f"Agent {name}"),
                         ("pri", "100")):
            sub(t, tag, val)
        title, text = HINTS.get(name, (f"Claude agent {name}", "Prompt for the agent."))
        input_dialog(t, 0, title, text, timeout=300)
        javascriptlet(t, 1, js_send(name), 15)
    t = sub(root, "Task", sr=f"task{chooser_id}")
    for tag, val in (("cdate", TIME), ("edate", TIME), ("id", chooser_id), ("nme", CHOOSER),
                     ("pri", "100")):
        sub(t, tag, val)
    # sort: AI advisors first in a fixed order, the phone agent last
    order = [n for n in LABELS if n in names] + [n for n in names if n not in LABELS]
    javascriptlet(t, 0, js_items(order), 5)
    panel.list_dialog(t, 1, "Ask which AI?", "%ai_items")
    javascriptlet(t, 2, js_pick(order), 5)
    return root


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", nargs="?")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    xml = to_xml(build(agents()))
    if a.dry_run:
        sys.stdout.write(xml)
        return 0
    if not a.out or not a.out.endswith(".prj.xml"):
        ap.error("need OUT ending in .prj.xml (Tasker's importer goes by the extension)")
    Path(a.out).write_text(xml)
    print(f"wrote {a.out}: tasks {', '.join('Agent ' + n for n in agents())}, {CHOOSER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
