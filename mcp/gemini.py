"""Drive the Gemini app on the phone: new chat, prompt, optional "+" tool, send, read the reply.

Used by the android-assistant MCP tool `ask_gemini`, and runnable on its own for debugging:
    python gemini.py "prompt" [--tool "Deep Research"] [--no-send] [--wait 60]

How it works (found on this phone, Gemini in Czech, Nov 2026):
  - An ACTION_SEND text/plain intent to the Gemini entry activity opens a NEW chat in the Gemini
    overlay (com.google.android.googlequicksearchbox ... robin.main.MainActivity) with the prompt
    already in the composer. No typing, so long, multi-line and non-ASCII prompts work.
  - "+" (Přidat přílohy / Add files) opens a bottom sheet: Photos/Camera/Files/Drive, then the
    tools: Create images, Videos, Music, Canvas, Deep Research, Guided Learning.
  - Rows near the bottom edge of the collapsed sheet miss taps, so the sheet is expanded first
    (tap its drag handle) and the row's clickable parent is tapped at fresh coordinates.
Labels are matched in Czech and English so a UI language switch does not break it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import unicodedata

DROID = os.environ.get("DROID", "/root/.claude/skills/android-control/scripts/droid")
DROID_OUT = os.environ.get("DROID_OUT", os.path.join(os.environ.get("TMPDIR", "/tmp"), "droid"))
ENTRY = "com.google.android.apps.bard/.shellapp.BardEntryPointActivity"
GEMINI_PKGS = ("com.google.android.apps.bard", "com.google.android.googlequicksearchbox")
PROMPT_MAX = 8000  # characters; Gemini accepts more, but an intent extra this big is plenty

# canonical tool -> labels seen in the "+" sheet (lowercase, accents stripped when compared)
TOOLS = {
    "deep_research": ["deep research", "hloubkovy vyzkum"],
    "image": ["vytvorit obrazky", "create images", "create image", "images", "image"],
    "video": ["vytvareni videi", "create videos", "create video", "videos", "video"],
    "music": ["vytvareni hudby", "create music", "music"],
    "canvas": ["canvas"],
    "guided_learning": ["uceni s vedenim", "guided learning"],
}
# what a user or model may write -> canonical tool
ALIASES = {
    "deep research": "deep_research", "deepresearch": "deep_research", "research": "deep_research",
    "dr": "deep_research",
    "image": "image", "images": "image", "generate image": "image", "create image": "image",
    "create images": "image", "imagen": "image", "picture": "image", "obrazek": "image",
    "video": "video", "videos": "video", "generate video": "video", "create video": "video",
    "veo": "video",
    "music": "music", "generate music": "music", "create music": "music", "song": "music",
    "canvas": "canvas", "code": "canvas", "write": "canvas",
    "guided learning": "guided_learning", "learning": "guided_learning", "tutor": "guided_learning",
    "learn": "guided_learning",
}
PLUS_LABELS = ["pridat prilohy", "add files", "add attachments", "open upload file menu", "upload"]
SEND_LABELS = ["odeslat", "send", "send message", "odeslat zpravu"]
PLAY_LABELS = ["prehrat", "play", "read aloud", "precist nahlas", "listen"]
SHARE_OK = ["sdilet", "share"]           # "content was added from another app - share it?"
# The dialog's other button. An answer's own action row also has a Share button (Gemini's makes
# a public link to the chat and opens the share sheet), but never one of these next to it.
SHARE_DISMISS = ["zrusit", "cancel", "ne", "no", "not now", "ted ne", "nyni ne", "zavrit", "close"]


def share_consent(ns: list[dict]) -> dict | None:
    """The Share button of the "use content from another app?" dialog, or None. It counts only
    as a Button with a dismiss Button on about the same row, so the Share button under an answer
    is never taken for it."""
    for ok in ns:
        if ok.get("cls") != "Button" or label(ok) not in SHARE_OK:
            continue
        for n in ns:
            if n is not ok and n.get("cls") == "Button" and label(n) in SHARE_DISMISS \
                    and abs(n.get("y", 0) - ok.get("y", 0)) < 200:
                return ok
    return None
START_RESEARCH = ["zahajit vyzkum", "start research", "spustit vyzkum"]
HANDLE_ID = "assistant_robin_add_attachment_bottomsheet"
INPUT_ID_HINT = "input"


class GeminiError(RuntimeError):
    pass


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.replace("\xa0", " "))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def resolve_tool(tool: str) -> str:
    """'+Deep Research', 'Generate Image', 'image' -> canonical key; '' -> ''."""
    t = norm(tool).lstrip("+").strip()
    if not t:
        return ""
    if t.replace(" ", "_") in TOOLS:
        return t.replace(" ", "_")
    if t in ALIASES:
        return ALIASES[t]
    for key, labels in TOOLS.items():
        if any(t == l or t in l or l in t for l in labels):
            return key
    raise GeminiError(f"unknown Gemini tool {tool!r}; use one of: "
                      + ", ".join(k.replace("_", " ") for k in TOOLS))


# ------------------------------------------------------------------ phone primitives
def _droid(*args: str, timeout: int = 40) -> str:
    p = subprocess.run([DROID, *args], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise GeminiError(f"droid {args[0]} failed: {(p.stderr or p.stdout).strip()[-300:]}")
    return p.stdout


def nodes() -> list[dict]:
    for attempt in range(3):     # uiautomator dump fails now and then while the screen animates
        try:
            _droid("ui")
            break
        except GeminiError:
            if attempt == 2:
                raise
            time.sleep(1.5)
    with open(os.path.join(DROID_OUT, "last_ui.json")) as f:
        return json.load(f)


def label(n: dict) -> str:
    return norm(n.get("text") or n.get("desc") or "")


def find(ns: list[dict], labels: list[str], exact: bool = False) -> dict | None:
    for want in labels:
        for n in ns:
            lab = label(n)
            if lab and (lab == want if exact else lab.startswith(want)):
                return n
    return None


def clickable_at(ns: list[dict], n: dict) -> tuple[int, int]:
    """The clickable node that carries this label: itself, or the nearest clickable node listed
    just before it (the parent row or button) whose centre is close to the label's."""
    if n.get("click"):
        return n["x"], n["y"]
    for m in reversed(ns[: n["i"]]):
        if m.get("click") and abs(m["y"] - n["y"]) < 90 and abs(m["x"] - n["x"]) < 300:
            return m["x"], m["y"]
    return n["x"], n["y"]


def tap(x: int, y: int) -> None:
    _droid("tap", str(x), str(y))


def in_gemini(ns: list[dict]) -> bool:
    return any(n.get("pkg") in GEMINI_PKGS for n in ns)


def composer(ns: list[dict]) -> dict | None:
    return next((n for n in ns if n.get("edit") and n.get("pkg") in GEMINI_PKGS), None)


def wait_for(pred, timeout: float = 8.0, step: float = 1.0) -> list[dict]:
    end = time.time() + timeout
    ns = nodes()
    while not pred(ns) and time.time() < end:
        time.sleep(step)
        ns = nodes()
    return ns


# ------------------------------------------------------------------ steps
def new_chat_with(prompt: str) -> list[dict]:
    q = prompt.replace("'", "'\\''")
    _droid("sh", f"am start -a android.intent.action.SEND -t text/plain "
                 f"--es android.intent.extra.TEXT '{q}' -n {ENTRY}")
    head = norm(prompt)[:20]
    ns = wait_for(lambda ns: (c := composer(ns)) is not None and norm(c["text"]).startswith(head[:10]),
                  timeout=10)
    c = composer(ns)
    if c is None:
        raise GeminiError("Gemini did not open a composer (is the phone locked?)")
    if not norm(c["text"]).startswith(head[:10]):
        raise GeminiError(f"composer does not hold the prompt; it shows {c['text'][:60]!r}")
    return ns


def pick_tool(key: str, ns: list[dict]) -> list[dict]:
    plus = find(ns, PLUS_LABELS)
    if plus is None:
        raise GeminiError("no '+' button in the Gemini composer")
    tap(*clickable_at(ns, plus))
    ns = wait_for(lambda ns: find(ns, TOOLS[key]) is not None, timeout=6)
    row = find(ns, TOOLS[key])
    if row is None:
        raise GeminiError(f"'+' menu has no {key.replace('_', ' ')} entry; it lists: "
                          + ", ".join(label(n) for n in ns if n.get("cls") == "TextView"))
    handle = next((n for n in ns if n.get("id") == HANDLE_ID), None)
    if handle is not None and any(w in label(handle) for w in ("sbaleno", "collapsed")):
        tap(handle["x"], handle["y"])          # expand: bottom rows of the collapsed sheet miss taps
        time.sleep(1.5)
        ns = nodes()
        row = find(ns, TOOLS[key]) or row
    tap(*clickable_at(ns, row))
    time.sleep(2.0)
    ns = nodes()
    if composer(ns) is None:
        raise GeminiError(f"after choosing {key} the composer is gone; screen: "
                          + "; ".join(label(n) for n in ns[:12]))
    return ns


PLAN_CARD = ("research plan with", "plan vyzkumu", "vyzkumny plan")


def start_button(ns: list[dict]) -> dict | None:
    """The plan's "Start research" button. Its label is drawn, not exposed (Nov 2026): the
    plan card ("Research plan with 3 steps") ends with two unlabeled clickable ViewGroups on
    one row, Edit plan (left) and Start research (right). A labelled button wins if present."""
    b = find(ns, START_RESEARCH)
    if b is not None:
        return b
    card = next((n for n in ns if label(n).startswith(PLAN_CARD)), None)
    if card is None:
        return None
    row = [n for n in ns if n.get("click") and n.get("cls") == "ViewGroup" and not label(n)
           and n["y"] > card["y"] and n.get("pkg") in GEMINI_PKGS]
    if len(row) < 2:
        return None
    y = max(n["y"] for n in row)
    pair = [n for n in row if abs(n["y"] - y) < 40]
    return max(pair, key=lambda n: n["x"]) if len(pair) == 2 else None


def selected_tool_chip(ns: list[dict], key: str) -> bool:
    """True when the composer area shows the chosen tool (a chip with its label)."""
    c = composer(ns)
    return any(find([n], TOOLS[key]) for n in ns if c and abs(n["y"] - c["y"]) < 250)


def send(ns: list[dict]) -> list[dict]:
    b = find(ns, SEND_LABELS, exact=True)
    if b is None:
        raise GeminiError("no send button in the Gemini composer")
    tap(*clickable_at(ns, b))
    time.sleep(2.0)
    ns = nodes()
    # Gemini asks before using text that arrived by share intent. The user asked for this
    # prompt to go to Gemini, so confirm; it is the same consent the user gave us.
    ok = share_consent(ns)
    if ok is not None:
        tap(*clickable_at(ns, ok))
        time.sleep(2.0)
        ns = nodes()
    return ns


CHROME = {"otevrit postranni panel", "open sidebar", "novy docasny chat", "new temporary chat",
          "pridat prilohy", "add files", "mikrofon", "microphone", "odeslat", "send", "seznam priloh",
          "odstranit", "remove", "zdroje", "sources", "soubory", "files", "gemini"}


FOOTER = ("gemini je ai", "gemini is ai", "gemini can make mistakes", "gemini muze delat chyby",
          # Gemini Live promo card under some answers
          "proberte tohle s gemini live", "vyzkousejte hlasovou konverzaci",
          "talk about this with gemini live", "try real-time voice", "chat about this with gemini live")
DONE = ("odpoved je hotova", "response is done", "response complete", "answer is ready")


def is_done(ns: list[dict]) -> bool:
    """Gemini announces a finished answer for accessibility ('Odpověď je hotová')."""
    return any(label(n).startswith(DONE) for n in ns)


def chat_text(ns: list[dict]) -> str:
    """Visible chat text in the Gemini overlay: TextViews minus header, buttons and composer."""
    out = []
    for n in ns:
        t = n.get("text") or ""
        if (t and n.get("pkg") in GEMINI_PKGS and not n.get("edit") and n.get("cls") == "TextView"
                and norm(t) not in CHROME and not norm(t).startswith(FOOTER) and n["y"] > 260):
            out.append(t)
    return "\n".join(out).strip()


def reply_text(ns: list[dict], prompt: str) -> str:
    """Chat text after the echo of our prompt (the whole chat text if the echo scrolled away)."""
    text = chat_text(ns)
    head = norm(prompt)[:30]
    lines = text.split("\n")
    for i, t in enumerate(lines):
        if head and norm(t).startswith(head):
            return "\n".join(lines[i + 1:]).strip()
    return text


def wait_reply(prompt: str, wait_s: int, step: float = 3.0) -> str:
    """Poll until the reply is non-empty and unchanged between two reads, or wait_s runs out."""
    end, last = time.time() + wait_s, None
    while True:
        ns = nodes()
        cur = reply_text(ns, prompt)
        if cur and (is_done(ns) or cur == last) or time.time() >= end:
            return cur
        last = cur
        time.sleep(step)


def read_aloud(ns: list[dict]) -> bool:
    """Tap Play (Přehrát) under the last answer: Gemini reads it out on the phone speaker."""
    plays = [n for n in ns if n.get("pkg") in GEMINI_PKGS and label(n) in PLAY_LABELS]
    if not plays:
        return False
    b = max(plays, key=lambda n: n["y"])     # the newest answer is lowest on screen
    tap(*clickable_at(ns, b))
    return True


def ask(prompt: str, tool: str = "", send_it: bool = True, wait_s: int = 0,
        start_research: bool = True, speak: bool = False, log=print) -> dict:
    prompt = prompt.strip()
    if not prompt:
        raise GeminiError("prompt is empty")
    if len(prompt) > PROMPT_MAX:
        raise GeminiError(f"prompt is {len(prompt)} characters; limit {PROMPT_MAX}")
    key = resolve_tool(tool)
    ns = new_chat_with(prompt)
    log("new chat, prompt in composer")
    chip = None
    if key:
        ns = pick_tool(key, ns)
        chip = selected_tool_chip(ns, key)
        log(f"tool {key}: chip {'shown' if chip else 'not seen in dump'}")
    out = {"tool": key or None, "tool_chip": chip, "sent": False, "reply": ""}
    if not send_it:
        return out
    ns = send(ns)
    out["sent"] = True
    log("sent")
    if key == "deep_research" and start_research:
        ns = wait_for(lambda ns: start_button(ns) is not None, timeout=max(wait_s, 90), step=3)
        b = start_button(ns)
        if b is not None:
            tap(*clickable_at(ns, b))
            out["research_started"] = True
            log("research plan confirmed")
            time.sleep(2)
            ns = nodes()
    out["reply"] = wait_reply(prompt, wait_s) if wait_s else reply_text(ns, prompt)
    if speak and key not in ("deep_research",):
        out["reading_aloud"] = read_aloud(nodes())
        log(f"read aloud: {out['reading_aloud']}")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--tool", default="")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--wait", type=int, default=0)
    ap.add_argument("--speak", action="store_true", help="press Play to read the answer aloud")
    ap.add_argument("--back-to", default="", help="package/activity to bring back afterwards")
    a = ap.parse_args()
    try:
        r = ask(a.prompt, a.tool, send_it=not a.no_send, wait_s=a.wait, speak=a.speak,
                log=lambda m: print("..", m, file=sys.stderr))
        print(json.dumps(r, ensure_ascii=False, indent=1))
    except GeminiError as e:
        print("error:", e, file=sys.stderr)
        sys.exit(1)
    finally:
        if a.back_to:
            _droid("sh", f"am start -n {a.back_to}")
