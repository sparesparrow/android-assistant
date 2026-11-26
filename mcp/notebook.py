"""Drive a Gemini Notebook (NotebookLM app) on the phone: chat with a notebook, add sources,
start Studio outputs. Used by the android-assistant MCP tools notebook_*; runnable for debugging:
    python notebook.py ask "question" [--notebook URL|ID] [--wait 60] [--no-send]
    python notebook.py add "text or https://url" [--notebook URL|ID] [--no-confirm]
    python notebook.py studio report [--notebook URL|ID]
    python notebook.py read [--notebook URL|ID]

Found on this phone (Nov 2026, NotebookLM app com.google.android.apps.labs.language.tailwind,
English UI, branded "Gemini Notebook"):
  - https://notebook.google.com/notebook/<uuid> opens the notebook when the intent is pinned to
    the app (-p); the domain is not set to open by default.
  - Tabs at the bottom: "Sources", "Chat", "Studio" (labels "Chat\\nTab 2 of 3").
  - The chat box is a Flutter field: `input text` cannot type non-ASCII and the paste key is
    ignored. Text therefore goes through the clipboard: Android's share sheet (ACTION_CHOOSER)
    has a "Copy" button (#copy), then long-press the field -> "Paste".
  - Sharing text/plain to .share.ShareIntentHandlerActivity shows "Select a Notebook" and adds
    the text (or a link) as a source of the picked notebook.
  - Studio: "Create an audio overview", "... video overview", "Create Slide Deck", "Create
    flashcards", "Create a quiz", "Create an infographic", "Create a report" (disabled while the
    notebook has no sources).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse

import gemini as g
from gemini import GeminiError, clickable_at, find, label, nodes, norm, tap

PKG = "com.google.android.apps.labs.language.tailwind"
SHARE = f"{PKG}/.share.ShareIntentHandlerActivity"
DEFAULT_NOTEBOOK = os.environ.get(
    "GEMINI_NOTEBOOK", "https://notebook.google.com/notebook/cbe9fa1f-9df8-4865-875e-c1b39496a955")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TEXT_MAX = 8000

STUDIO = {
    "audio": ["create an audio overview"], "video": ["create a video overview"],
    "slides": ["create slide deck"], "flashcards": ["create flashcards"],
    "quiz": ["create a quiz"], "infographic": ["create an infographic"],
    "report": ["create a report"],
}
STUDIO_ALIASES = {
    "audio overview": "audio", "podcast": "audio", "audio": "audio",
    "video overview": "video", "video": "video",
    "slide deck": "slides", "slides": "slides", "deck": "slides", "presentation": "slides",
    "flashcards": "flashcards", "cards": "flashcards", "quiz": "quiz", "test": "quiz",
    "infographic": "infographic", "report": "report", "reports": "report", "briefing": "report",
}
CHROME = {"back", "share notebook", "more options", "send", "dismiss banner", "camera",
          "record audio", "add a source", "talk with your notebook in real time",
          "gemini notebook can be inaccurate, so double-check."}


# ------------------------------------------------------------------ notebook navigation
def notebook_url(notebook: str = "") -> str:
    """URL or bare id -> canonical notebook URL; anything else is refused."""
    n = (notebook or DEFAULT_NOTEBOOK).strip()
    m = UUID.search(n.lower())
    host_ok = n.startswith(("https://notebook.google.com/", "https://notebooklm.google.com/")) or UUID.fullmatch(n.lower())
    if not m or not host_ok:
        raise GeminiError("notebook must be a notebook.google.com / notebooklm.google.com link or its id")
    return f"https://notebook.google.com/notebook/{m.group(0)}"


def in_notebook(ns: list[dict]) -> bool:
    return any(n.get("pkg") == PKG and label(n).startswith("chat") and "tab 2 of 3" in label(n) for n in ns)


def title_of(ns: list[dict]) -> str:
    back = find(ns, ["back"], exact=True)
    if back is None:
        return ""
    after = [n for n in ns if n["i"] > back["i"] and (n.get("text") or n.get("desc"))
             and not n.get("click") and abs(n["y"] - back["y"]) < 60]
    return (after[0].get("text") or after[0].get("desc")) if after else ""


def open_notebook(notebook: str = "") -> list[dict]:
    url = notebook_url(notebook)
    g._droid("sh", f"am start -a android.intent.action.VIEW -d {url} -p {PKG}")
    ns = g.wait_for(in_notebook, timeout=15, step=1.5)
    if not in_notebook(ns):
        raise GeminiError("the notebook did not open (signed out, no access, or phone locked?)")
    return ns


def tab(ns: list[dict], name: str) -> list[dict]:
    t = next((n for n in ns if n.get("pkg") == PKG and label(n).startswith(name)
              and "tab " in label(n)), None)
    if t is None:
        raise GeminiError(f"no {name} tab")
    tap(t["x"], t["y"])
    time.sleep(1.5)
    return nodes()


def editor(ns: list[dict]) -> dict | None:
    return next((n for n in ns if n.get("edit") and n.get("pkg") == PKG), None)


# ------------------------------------------------------------------ text entry via clipboard
def to_clipboard(text: str) -> None:
    """Android share sheet -> Copy: works for any text, no Termux:API or IME needed.
    (The clipboard is recorded by clipboard managers such as cliphist.)"""
    inner = ("intent:#Intent;action=android.intent.action.SEND;type=text/plain;"
             "S.android.intent.extra.TEXT=" + urllib.parse.quote(text, safe="") + ";end")
    # Only ever tap the element with id "copy": the sheet also lists direct-share targets
    # (people in WhatsApp etc.), so a coordinate tap on a half-drawn sheet could share the text.
    for _ in range(2):                       # the sheet is sometimes slow to appear
        g._droid("sh", f"am start -a android.intent.action.CHOOSER --eu android.intent.extra.INTENT '{inner}'")
        ns = g.wait_for(lambda ns: copy_button(ns) is not None, timeout=15, step=1.5)
        copy = settled(copy_button(ns))
        if copy is not None:
            tap(copy["x"], copy["y"])
            time.sleep(1.0)
            if not in_quick_share(nodes()):
                return
            # The tap hit Quick Share instead of Copy; leave it without sending anything.
        g._droid("key", "back")
    raise GeminiError("the share sheet showed no Copy button that could be tapped")


def copy_button(ns: list[dict]) -> dict | None:
    return next((n for n in ns if n.get("id") == "copy"), None)


def settled(copy: dict | None, tries: int = 4) -> dict | None:
    """The share sheet slides up after it is first drawn, so a Copy button read too early sits
    lower than where it ends up, over the Quick Share row. Re-read until two reads agree."""
    for _ in range(tries):
        if copy is None:
            return None
        time.sleep(0.6)
        again = copy_button(nodes())
        if again is not None and (again["x"], again["y"]) == (copy["x"], copy["y"]):
            return again
        copy = again
    return None


def in_quick_share(ns: list[dict]) -> bool:
    return any(n.get("pkg") == "com.google.android.gms" and label(n) in ("quick share", "send to nearby devices")
               for n in ns)


def paste_into(ns: list[dict], text: str) -> list[dict]:
    """Paste the clipboard (holding `text`, see to_clipboard) into the notebook's editor,
    replacing what was there, and verify it. Copy first, then open the notebook: the share
    sheet must not sit on top of the notebook while we look for the field."""
    e = editor(ns)
    if e is None:
        raise GeminiError("no text field on the notebook screen")
    tap(e["x"], e["y"])
    time.sleep(1.0)
    g._droid("sh", "input keycombination 113 29; input keyevent 67")   # Ctrl+A, Del: clear
    time.sleep(0.5)
    e = editor(nodes()) or e          # the keyboard moved the field up
    g._droid("longtap", str(e["x"]), str(e["y"]))
    time.sleep(1.2)
    ns = nodes()
    paste = find(ns, ["paste", "vlozit"], exact=True)
    if paste is None:
        raise GeminiError("no Paste menu on the text field")
    tap(*clickable_at(ns, paste))
    time.sleep(1.0)
    ns = nodes()
    e = editor(ns)
    if e is None or not norm(e.get("text", "")).startswith(norm(text)[:15]):
        raise GeminiError(f"the field does not hold the text; it shows {(e or {}).get('text', '')[:60]!r}")
    return ns


# ------------------------------------------------------------------ reading
def chat_text(ns: list[dict]) -> str:
    out = []
    for n in ns:
        t = (n.get("text") or n.get("desc") or "").strip()
        if (not t or n.get("pkg") != PKG or n.get("edit") or norm(t) in CHROME
                or "tab " in norm(t) and "of 3" in norm(t) or n["y"] < 260):
            continue
        if n.get("cls") == "Button" and len(t) < 40:     # action chips, copy/like buttons
            continue
        out.append(t)
    text = "\n".join(dict.fromkeys(out)).replace("\ufffc", "")
    return re.sub(r"^(\d+\.\s*\n)+", "", text.strip()).strip()   # empty list markers before the items


def reply_after(ns: list[dict], prompt: str) -> str:
    text = chat_text(ns)
    head = norm(prompt)[:30]
    parts = text.split("\n")
    for i in range(len(parts) - 1, -1, -1):          # the newest copy of our question
        if head and norm(parts[i]).startswith(head):
            return "\n".join(parts[i + 1:]).strip()
    return text


def wait_reply(prompt: str, wait_s: int, step: float = 3.0) -> str:
    end, last = time.time() + wait_s, None
    while True:
        cur = reply_after(nodes(), prompt)
        if cur and cur == last or time.time() >= end:
            return cur
        last = cur
        time.sleep(step)


def copy_reply() -> str:
    """Whole newest answer, as Markdown: tap its Copy button, paste into the chat box, read the
    box, clear it. Works for answers far longer than the screen. Needs the chat tab open with
    the newest answer's buttons visible (it is, right after an answer finishes)."""
    for _ in range(3):
        ns = nodes()
        copies = [n for n in ns if n.get("pkg") == PKG and label(n) == "copy"]
        if copies:
            break
        g._droid("swipe", "540", "1500", "540", "700")      # scroll to the bottom
        time.sleep(0.8)
    else:
        return ""
    c = max(copies, key=lambda n: n["y"])
    tap(c["x"], c["y"])
    time.sleep(1.5)
    ns = nodes()
    e = editor(ns)
    if e is None:
        return ""
    tap(e["x"], e["y"])
    time.sleep(1.2)
    e = editor(nodes()) or e
    g._droid("longtap", str(e["x"]), str(e["y"]))
    time.sleep(1.2)
    ns = nodes()
    p = find(ns, ["paste", "vlozit"], exact=True)
    if p is None:
        return ""
    tap(*clickable_at(ns, p))
    time.sleep(2.0)
    e = editor(nodes())
    text = (e or {}).get("text", "")
    g._droid("sh", "input keycombination 113 29; input keyevent 67")   # leave the box empty
    g._droid("key", "back")
    return text.strip()


def collect_reply(prompt: str, max_swipes: int = 25) -> str:
    """Whole answer, also when it is longer than the screen: scroll back to the echoed
    question, then page down collecting lines until nothing new appears."""
    head = norm(prompt)[:30]
    for _ in range(max_swipes):                      # back to the question
        if any(norm(t).startswith(head) for t in chat_text(nodes()).split("\n")):
            break
        g._droid("swipe", "540", "700", "540", "1500")
        time.sleep(0.8)
    seen: list[str] = []
    stale = 0
    for _ in range(max_swipes):
        new = [l for l in chat_text(nodes()).split("\n") if l not in seen]
        seen += new
        stale = 0 if new else stale + 1
        if stale >= 2:
            break
        g._droid("swipe", "540", "1500", "540", "750")
        time.sleep(0.8)
    return reply_after([{"text": l, "desc": "", "pkg": PKG, "cls": "View", "edit": False,
                         "click": False, "y": 1000, "i": i} for i, l in enumerate(seen)], prompt)


# ------------------------------------------------------------------ actions
def ask(prompt: str, notebook: str = "", send_it: bool = True, wait_s: int = 60,
        save_note: bool = False, full: bool = False, log=print) -> dict:
    prompt = prompt.strip()
    if not prompt:
        raise GeminiError("prompt is empty")
    if len(prompt) > TEXT_MAX:
        raise GeminiError(f"prompt is {len(prompt)} characters; limit {TEXT_MAX}")
    notebook_url(notebook)                 # refuse a bad link before touching the phone
    to_clipboard(prompt)
    ns = tab(open_notebook(notebook), "chat")
    title = title_of(ns)
    ns = paste_into(ns, prompt)
    log(f"notebook {title!r}: question in the chat box")
    out = {"notebook": title, "sent": False, "reply": ""}
    if not send_it:
        return out
    b = find(ns, ["send"], exact=True)
    if b is None:
        raise GeminiError("no Send button")
    tap(*clickable_at(ns, b))
    out["sent"] = True
    log("sent")
    time.sleep(3)
    out["reply"] = wait_reply(prompt, wait_s) if wait_s else reply_after(nodes(), prompt)
    if full and out["reply"]:
        out["reply"] = copy_reply() or collect_reply(prompt) or out["reply"]
    if save_note and out["reply"]:
        if full:
            g._droid("swipe", "540", "1500", "540", "700")
            time.sleep(0.8)
        ns = nodes()
        saves = [n for n in ns if n.get("pkg") == PKG and label(n) == "save to note"]
        if saves:
            tap(*clickable_at(ns, max(saves, key=lambda n: n["y"])))   # under the newest answer
            time.sleep(1.5)
        out["saved_to_note"] = bool(saves)
        log(f"saved to note: {bool(saves)}")
    return out


def add_source(content: str, notebook: str = "", confirm: bool = True, log=print) -> dict:
    """Share text or a link into the notebook as a source (share sheet -> Select a Notebook)."""
    content = content.strip()
    if not content:
        raise GeminiError("nothing to add")
    if len(content) > 100_000:
        raise GeminiError("source text longer than 100000 characters")
    title = title_of(open_notebook(notebook))
    if not title:
        raise GeminiError("could not read the notebook's title")
    q = content.replace("'", "'\\''")
    g._droid("sh", f"am start -a android.intent.action.SEND -t text/plain "
                   f"--es android.intent.extra.TEXT '{q}' -n {SHARE}")
    ns = g.wait_for(lambda ns: find(ns, ["select a notebook"]) is not None, timeout=10)
    want = norm(title)
    row = next((n for n in ns if n.get("click") and want in label(n).split("\n")[1:2]), None) or \
        next((n for n in ns if n.get("click") and want in label(n)), None)
    if row is None:
        g._droid("key", "back")
        raise GeminiError(f"notebook {title!r} is not in the Select a Notebook list")
    if not confirm:
        g._droid("key", "back")
        return {"notebook": title, "added": False, "note": "found in the list; not confirmed"}
    tap(row["x"], row["y"])
    time.sleep(3)
    ns = nodes()
    log(f"picked {title!r}")
    # follow-up confirmation, if any ("Add", "Insert", "Import")
    ok = find(ns, ["add", "insert", "import", "pridat", "vlozit"], exact=True)
    if ok is not None and ok.get("pkg") == PKG:
        tap(*clickable_at(ns, ok))
        time.sleep(3)
        ns = nodes()
    return {"notebook": title, "added": True,
            "screen": "; ".join(label(n) for n in ns if n.get("pkg") == PKG and label(n))[:400]}


def resolve_studio(output: str) -> str:
    o = norm(output).lstrip("+").strip()
    if o in STUDIO:
        return o
    if o in STUDIO_ALIASES:
        return STUDIO_ALIASES[o]
    raise GeminiError("unknown Studio output; use one of: audio, video, slides, flashcards, "
                      "quiz, infographic, report")


def studio(output: str, notebook: str = "", log=print) -> dict:
    key = resolve_studio(output)
    ns = tab(open_notebook(notebook), "studio")
    title = title_of(ns)
    b = find(ns, STUDIO[key])
    if b is None:
        raise GeminiError(f"Studio has no {key} button")
    btn = next((n for n in ns if n.get("cls") == "Button" and label(n).startswith(STUDIO[key][0])), b)
    if not btn.get("click"):
        raise GeminiError(f"{key} is disabled: the notebook needs at least one source")
    tap(btn["x"], btn["y"])
    time.sleep(3)
    ns = nodes()
    log(f"studio {key} requested")
    return {"notebook": title, "output": key, "started": True,
            "screen": "; ".join(label(n) for n in ns if n.get("pkg") == PKG and label(n))[:400]}


def read(notebook: str = "", which: str = "chat") -> str:
    """Visible text of a notebook tab (chat, sources or studio); opens the notebook first."""
    if which not in ("chat", "sources", "studio"):
        raise GeminiError("which must be chat, sources or studio")
    ns = tab(open_notebook(notebook), which)
    return chat_text(ns) or "nothing visible"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["ask", "add", "studio", "read"])
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--notebook", default="")
    ap.add_argument("--wait", type=int, default=60)
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--no-confirm", action="store_true")
    ap.add_argument("--save-note", action="store_true")
    ap.add_argument("--full", action="store_true", help="scroll to collect a long answer")
    ap.add_argument("--back-to", default="")
    a = ap.parse_args()
    lg = lambda m: print("..", m, file=sys.stderr)  # noqa: E731
    try:
        if a.action == "ask":
            r = ask(a.text, a.notebook, not a.no_send, a.wait, a.save_note, a.full, lg)
        elif a.action == "add":
            r = add_source(a.text, a.notebook, not a.no_confirm, lg)
        elif a.action == "studio":
            r = studio(a.text, a.notebook, lg)
        else:
            r = read(a.notebook, a.text or "chat")
        print(json.dumps(r, ensure_ascii=False, indent=1) if isinstance(r, dict) else r)
    except GeminiError as e:
        print("error:", e, file=sys.stderr)
        sys.exit(1)
    finally:
        if a.back_to:
            g._droid("sh", f"am start -n {a.back_to}")
