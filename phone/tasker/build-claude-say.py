#!/usr/bin/env python3
"""Build the Tasker task "Claude Say": speak a text with Android's own TTS (Google), no API key.

The text is a file, not an intent extra (am's list extras split on commas):
  1. Read File  assistant/say.txt (relative to /sdcard) -> %say_text
  2. Say        %say_text with the default engine and voice (Settings > Text-to-speech output),
                on the media stream, waiting until it has been said.
voice/android-say.sh pushes the file and starts the task with Tasker's ACTION_TASK broadcast.

  build-claude-say.py OUT.prj.xml      then: tasker-edit.py pull; edit --add-tasks OUT "Claude Assistant"; push
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("panel", HERE / "build-claude-panel.py")
panel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(panel)
sub, s_arg, i_arg, to_xml = panel.sub, panel.s_arg, panel.i_arg, panel.to_xml

PROJECT = "Claude Say"
TASK = "Claude Say"
SAY_FILE = "assistant/say.txt"      # relative to /sdcard, as Tasker's file actions take it
TASK_ID = 9351
TIME = "1795700000000"


def build() -> ET.Element:
    root = ET.Element("TaskerData", sr="", dvi="1", tv="6.7.6-beta")
    proj = sub(root, "Project", sr="proj0", ve="2")
    sub(proj, "cdate", TIME)
    sub(proj, "id", str(uuid.uuid5(uuid.NAMESPACE_URL, "claude-say-tasker-project")))
    sub(proj, "name", PROJECT)
    sub(proj, "tids", str(TASK_ID))
    t = sub(root, "Task", sr=f"task{TASK_ID}")
    for tag, val in (("cdate", TIME), ("edate", TIME), ("id", TASK_ID), ("nme", TASK), ("pri", "100")):
        sub(t, tag, val)
    a = sub(t, "Action", sr="act0", ve="7")          # 417 Read File: path, to var, (1)
    sub(a, "code", "417")
    s_arg(a, 0, SAY_FILE)
    s_arg(a, 1, "%say_text")
    i_arg(a, 2, 1)
    a = sub(t, "Action", sr="act1", ve="7")          # 559 Say
    sub(a, "code", "559")
    s_arg(a, 0, "%say_text")
    s_arg(a, 1, "default:default")                   # engine:voice = the system default (Google TTS)
    i_arg(a, 2, 3)                                   # stream: media
    i_arg(a, 3, 5)                                   # pitch
    i_arg(a, 4, 5)                                   # speed
    i_arg(a, 5, 1)                                   # respect audio focus
    i_arg(a, 6, 0)                                   # network: no
    i_arg(a, 7, 0)                                   # continue task immediately: no (wait)
    return root


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", nargs="?")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    xml = to_xml(build())
    if a.dry_run:
        sys.stdout.write(xml)
        return 0
    if not a.out or not a.out.endswith(".prj.xml"):
        ap.error("need OUT ending in .prj.xml (Tasker's importer goes by the extension)")
    Path(a.out).write_text(xml)
    print(f"wrote {a.out}: task {TASK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
