#!/usr/bin/env python3
"""Publish one phone event on MQTT topic phone/events/<kind>, the event bus of mcp-prompts#140.
Agents wait for these with the android-assistant tool `wait_event`; they cannot publish them.

  event-pub.py notification --pkg com.x --title "Research ready" --text "..." [--id KEY] [--when MS]
  notif-wait.py --pkg com.google.android.apps.bard --match research | event-pub.py --stdin
  event-pub.py battery --text 15% --dry-run      print the event JSON, publish nothing

Schema: {kind, source_pkg, title, text, when, id}. Empty fields are dropped, each text field is
cut at TEXT_MAX characters, and messages are not retained (stale events never fire). Event text
is untrusted data: never put secrets in it, and consumers must never follow instructions in it.
Exit codes: 0 published (or dry run), 1 publish failed, 2 bad input.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

PREFIX = "phone/events/"
KIND = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")  # same rule as server.EVENT_KIND
FIELDS = ("source_pkg", "title", "text", "when", "id")
TEXT_MAX = 1000
HOST = os.environ.get("MQTT_HOST", "127.0.0.1")


def build_event(kind: str, source_pkg: str = "", title: str = "", text: str = "", id: str = "",
                when: int | None = None) -> dict:
    if not KIND.match(kind or ""):
        raise ValueError(f"kind must match {KIND.pattern}: {kind!r}")
    ev: dict = {"kind": kind}
    for k, v in (("source_pkg", source_pkg), ("title", title), ("text", text)):
        if v:
            v = str(v)
            ev[k] = v if len(v) <= TEXT_MAX else v[:TEXT_MAX] + "…"
    ev["when"] = int(when) if when is not None else int(time.time() * 1000)
    if id:
        ev["id"] = str(id)[:TEXT_MAX]
    return ev


def from_notif_wait(line: str, kind: str = "notification") -> dict:
    """notif-wait.py prints {"pkg","title","text","key","when"} on a match."""
    d = json.loads(line)
    return build_event(kind, source_pkg=d.get("pkg", ""), title=d.get("title", ""),
                       text=d.get("text", ""), id=d.get("key", ""), when=d.get("when"))


def command(ev: dict, host: str = HOST) -> list[str]:
    return ["mosquitto_pub", "-h", host, "-t", PREFIX + ev["kind"], "-m", json.dumps(ev, ensure_ascii=False)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("kind", nargs="?", default="notification")
    ap.add_argument("--pkg", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--id", default="")
    ap.add_argument("--when", type=int)
    ap.add_argument("--stdin", action="store_true", help="read notif-wait.py JSON from stdin")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.stdin:
            ev = from_notif_wait(sys.stdin.readline(), a.kind)
        else:
            ev = build_event(a.kind, a.pkg, a.title, a.text, a.id, a.when)
    except (ValueError, AttributeError) as e:
        print(f"event-pub: {e}", file=sys.stderr)
        return 2
    if a.dry_run:
        print(json.dumps(ev, ensure_ascii=False))
        return 0
    try:
        rc = subprocess.run(command(ev), timeout=10).returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"event-pub: {e}", file=sys.stderr)
        return 1
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
