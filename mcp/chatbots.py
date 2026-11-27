"""Drive other chat apps on the phone the way gemini.py drives Gemini: DeepSeek, Kimi, Grok,
ChatGPT and Perplexity. One generic flow, one PROFILES entry per app.

    python chatbots.py <app> "prompt" [--mode X] [--no-send] [--wait N] [--dump DIR]

Flow of ask():
  1. ACTION_SEND text/plain to the app's share component puts the prompt in a (new) chat's
     composer. If the composer does not hold the prompt after 10 s (or the profile says
     text_entry="paste"), the prompt goes through the clipboard instead: notebook.to_clipboard,
     open the app, "new chat" if offered, long-press the composer -> Paste.
  2. Optional mode (DeepThink, Search, Think, Pro, ...): tap its toggle in the composer area.
     uiautomator dumps carry no checked/selected state, so the toggle is verified only when a
     node has such a hint; otherwise it is just logged.
  3. Send (and accept a "share content from another app?" dialog, like gemini.send).
  4. With wait_s: poll until no Stop button is shown and the reply is the same in two reads.
  5. reply_via="copy" (ChatGPT, whose answer text is not in the uiautomator tree): wait until
     the answer's Copy button shows, tap it, paste into the empty composer, read, clear.
     reply_via="scroll" (Perplexity, whose long prompt echo fills the screen): fling to the
     end until the follow-ups show, then drag back up page by page and merge the text;
     Perplexity's sources are then read from the thread's Sources tab.

--dump DIR saves each step's raw node list (01-shared.json, 02-mode.json, 03-sent.json,
04-reply.json) for calibrating the labels below.

Labels marked "# calibrate" are guesses (English + Czech, compared after gemini.norm()) and are
to be replaced with what real dumps show.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import gemini
import notebook
from gemini import _droid, clickable_at, find, label, nodes, norm, tap, wait_for

APPS = ("deepseek", "kimi", "grok", "chatgpt", "perplexity")
PROMPT_MAX = 8000
SHARE_WAIT = 10          # seconds for the shared prompt to show up in the composer
PASTE_LABELS = ["paste", "vlozit"]
CLEAR_KEYS = "input keycombination 113 29; input keyevent 67"     # Ctrl+A, Del


class ChatbotError(gemini.GeminiError):
    pass


def _profile(**kw) -> dict:
    p = {
        "pkg": "",
        "share": "",               # component receiving ACTION_SEND text/plain
        "launch": None,            # component to start; None = the launcher intent (monkey -p)
        "composer": "",            # resource-id substring of the composer; "" = first EditText
        "composer_hidden": False,  # composer missing from the uiautomator tree: the send button
                                   # stands in for it and its text cannot be verified
        "send_labels": ["send", "odeslat", "send message", "odeslat zpravu"],
        "stop_labels": ["stop", "zastavit", "stop generating", "stop responding"],
        "new_chat_labels": ["new chat", "novy chat"],
        "mode_menu_labels": [],    # button that reveals the mode toggles, if they are hidden
        "chrome": set(),           # exact (normalised) strings that are UI, not chat
        "footer": (),              # prefixes of disclaimers and other non-answer text
        "end_labels": [],          # headings (text or icon desc) after which nothing is part
                                   # of the answer
        "end_ids": [],             # the same as resource-id substrings (label-less containers)
        "chrome_re": "",           # regex; normalised texts it fully matches are UI, not chat
        "jump_labels": [],         # button that scrolls the chat to the newest message;
                                   # wait_reply taps it whenever it is shown
        "modes": {},
        "text_entry": "share",     # "share" (paste as fallback) or "paste"
        "paste_via": "menu",       # "menu": long-press -> Paste; "key": KEYCODE_PASTE (279)
        "send_tap": "clickable",   # "clickable": tap the clickable node carrying the send label;
                                   # "self": tap the label node itself (Kimi: the send arrow is
                                   # a non-clickable View right after the clickable voice button)
        "login_labels": [],        # texts of a login wall; seeing one aborts with a clear error
        "reply_via": "screen",     # "screen": read the answer's text nodes; "copy": the answer
                                   # is not in the uiautomator tree (ChatGPT), so tap the Copy
                                   # button under it, paste into the empty composer, read the
                                   # composer and clear it again; "scroll": the answer is in
                                   # the tree but taller than the screen and there is no jump
                                   # button (Perplexity), so fling to the end, then drag back
                                   # up page by page, merging the pages' text
        "copy_labels": ["copy", "kopirovat"],   # the answer's Copy button (reply_via="copy")
        "scroll_span": 330,        # reply_via="scroll": drag between top+40 and top+span; must
                                   # stay above overlays that swallow swipes (Perplexity upsell)
        "hide_keyboard": False,    # the keyboard left up by pasting hides the mode buttons:
                                   # press Back once when no mode toggle or menu is shown
        "top": 200,                # y below which the chat starts (header above)
    }
    p.update(kw)
    return p


COMMON_CHROME = {"copy", "kopirovat", "share", "sdilet", "like", "dislike", "regenerate",
                 "znovu vygenerovat", "edit", "upravit", "microphone", "mikrofon", "voice",
                 "hlas", "menu", "new chat", "novy chat", "attach", "prilozit"}

PROFILES: dict[str, dict] = {
    "deepseek": _profile(
        # Calibrated on DeepSeek 2.9.0 (English UI), 2026-11-25, new-chat screen only: the text
        # field is not in the uiautomator tree at all, so the prompt cannot be read back.
        # Send is an icon: clickable View#chat_send whose child has desc "Send"; New chat is
        # View#chat_new (child desc "New chat"). Think/Search are checkable Views (checked state
        # is in the raw XML on the clickable parent, but droid drops it) - they are TOGGLES and
        # remember their state, so tapping one that is already on turns it off.
        pkg="com.deepseek.chat",
        share="com.deepseek.chat/.ShareProxyActivity",
        composer_hidden=True,
        send_labels=["send", "odeslat", "send message"],
        stop_labels=["stop", "zastavit", "stop generating"],                  # calibrate
        new_chat_labels=["new chat", "novy chat"],
        chrome={"deepseek", "hello, let's start chatting", "think", "deepthink", "search",
                "hluboke mysleni", "hledat", "message deepseek", "zprava deepseek"},
        footer=("ai-generated", "generovano ai", "obsah vygenerovan ai"),     # calibrate
        modes={"deepthink": ["deepthink", "think", "deepthink (r1)", "hluboke mysleni"],
               "search": ["search", "hledat"]},
        top=230,                   # chat_top_bar is [0,0][1080,228]
    ),
    "kimi": _profile(
        # Calibrated on Kimi 3.1.8 (English UI, not logged in), 2026-11-25.
        # Sharing text/plain to ExternalLinkActivity is accepted but ignored while logged out
        # (and once, from a cold start, it opened the login wall), so the prompt is pasted:
        # the composer is a readable EditText (hint "Ask me. Task me." is a separate TextView),
        # long-press shows no Paste menu in the tree, but KEYCODE_PASTE works.
        # Send is a NON-clickable View desc "Send message" listed right after the clickable
        # "Switch to voice input" button, so clickable_at would hit the mic: tap it directly.
        # There are no Thinking/Search toggles in 3.1.8: the header "K3 · High" opens a model
        # sheet (K3.1 Pro / K2.8 Preview / Instant, "Thinking effort", "Context length").
        pkg="com.moonshot.kimichat",
        share="com.moonshot.kimichat/.route.ExternalLinkActivity",
        text_entry="paste",
        paste_via="key",
        send_tap="self",
        send_labels=["send message", "send", "odeslat"],
        stop_labels=["stop", "zastavit", "stop generating"],                  # calibrate
        new_chat_labels=["new chat", "novy chat", "new conversation"],        # calibrate
        login_labels=["continue with google", "phone login"],
        chrome={"kimi", "hi , what would you like to do with kimi today?", "log in",
                "new project", "3d make", "slides", "swarm", "ask me. task me.", "k3", "high",
                "thinking", "search", "hledat"},
        footer=("kimi can make mistakes", "ai-generated", "content generated by ai"),  # calibrate
        modes={"thinking": ["thinking", "think", "k1.5", "premysleni"],      # absent in 3.1.8
               "search": ["search", "web search", "hledat"]},                 # absent in 3.1.8
        top=260,                   # header row (menu, "K3 High", voice) is centred at y=172
    ),
    "grok": _profile(
        # Calibrated on Grok 1.2.46 (English UI, used without logging in), 2026-11-25, on one
        # real review request (6.7k-character prompt, Fast model).
        # Sharing text/plain to GrokActivity opens the Ask tab with EXACTLY the shared text in
        # EditText#chat_text_input; a draft left in the composer is replaced, not prepended.
        # Send: clickable View#input_send_button, its child View has desc "Send message". With
        # an empty composer the same spot shows "Start Grok Voice"/"Speak" instead.
        # No Think/DeepSearch toggles in 1.2.46: the composer has a model chip ("Fast", with
        # Auto/Expert/Heavy/Build in a popup that is NOT in the uiautomator tree), so modes
        # cannot be picked or verified. Fast is the plain mode and the default.
        # After sending a long prompt the chat stays scrolled to the top of the echo (one tall
        # TextView holding the whole prompt) and shows a "Scroll to bottom" button.
        # The answer is TextViews with bullets as separate "•" nodes, between a "Thought for
        # 2s" chip and a "16 pages" sources chip; then icon buttons (Copy message, Share this
        # conversation, Like/Dislike, Read aloud, Regenerate this response) and follow-up
        # suggestion rows, which are not part of the answer.
        # Header: drawer, Ask/Imagine/Build tabs, then "Private Chat" (new chat screen) or
        # "Start new chat" (in a chat). No Stop button was seen: Fast answered in about 2 s.
        pkg="ai.x.grok",
        share="ai.x.grok/.main.GrokActivity",
        composer="chat_text_input",
        send_labels=["send message", "send", "odeslat", "submit"],
        stop_labels=["stop", "stop generating", "stop response", "zastavit"],  # not seen yet
        new_chat_labels=["start new chat", "new chat", "novy chat"],
        jump_labels=["scroll to bottom"],
        login_labels=["sign in with x", "continue with google", "continue with apple"],  # guess
        chrome={"grok", "ask", "imagine", "build", "try premium", "try skills", "create videos",
                "fast", "auto", "expert", "heavy", "speak", "ask anything (ai agent)",
                "ask anything", "private chat", "think", "deepsearch"},
        chrome_re=r"thought for \d+(\.\d+)?\s*(s|m|sec|min).*|\d+ (pages|sources|web pages|posts)",
        footer=("grok can make mistakes", "grok muze delat chyby"),
        end_labels=["copy message", "regenerate this response"],              # icon descs
        modes={"think": ["think", "premyslet", "premysleni"],                 # absent in 1.2.46
               "deepsearch": ["deepsearch", "deep search", "deepersearch"]},  # absent in 1.2.46
        top=260,                   # header row (drawer, Ask/Imagine/Build, new chat) at y=197
    ),
    "chatgpt": _profile(
        # Calibrated on ChatGPT 1.2026.321 (English UI, logged in, free plan), 2026-11-25, on
        # one real review request (6.7k-character prompt, default model, no tool picked).
        # MainActivity is the only ACTION_SEND text/plain target; sharing opens a NEW chat
        # (header: drawer, "Rejoin Plus" upsell, "Temporary chat") with EXACTLY the shared text
        # in the only EditText. Send: desc "Send Message" (with an empty composer the same spot
        # is "Start a voice conversation"). While answering, the same spot is desc "Stop".
        # THE ANSWER IS NOT IN THE UIAUTOMATOR TREE: only the prompt echo, "Show more",
        # progress lines ("Fetching Pull Request Diff and Files", "Thinking", "Worked for 45s")
        # and, once done, the action row Copy / Read aloud / Share / More actions are. So the
        # answer is read with reply_via="copy" (Copy -> paste into the composer -> read ->
        # clear), which gives the full Markdown. The dump also fails now and then while the
        # answer streams (screen never idle); wait_reply keeps polling through that.
        # No Search toggle in this version: "+" (desc "Attachment") opens Camera / Photos /
        # Files / Plugins / Think harder, and Plugins inserts "@" into the composer and lists
        # Create image / Sketch / Thinking / Deep research / Study / connected apps. Both
        # popups are NOT in the uiautomator tree, so modes cannot be picked or verified and
        # pick_mode fails without tapping anything (mode_menu_labels is empty on purpose:
        # "attach" would open the "+" popup and leave it open).
        # The phone auto-rotates: in landscape the drawer (chat history) stays open on the
        # left half and the chat moves to x > 1000.
        pkg="com.openai.chatgpt",
        share="com.openai.chatgpt/.MainActivity",
        send_labels=["send message", "send", "send prompt", "odeslat", "odeslat zpravu"],
        stop_labels=["stop", "stop generating", "stop streaming", "zastavit"],
        new_chat_labels=["new chat", "novy chat"],     # desc of the pencil icon in a chat
        jump_labels=["scroll to the bottom", "scroll to bottom"],
        login_labels=["log in", "sign up", "continue with google", "prihlasit se"],  # guess
        reply_via="copy",
        chrome={"chatgpt", "rejoin plus", "temporary chat", "ask chatgpt", "reply to chatgpt",
                "show more", "show less", "navigation menu", "navigate up", "edit menu",
                "attachment", "dictation", "start a voice conversation",
                "expand message editor", "create an image or sticker", "write or edit",
                "read aloud", "more actions", "thinking", "zeptejte se chatgpt"},
        chrome_re=r"(worked|thought) for \d+(\.\d+)?\s*(s|m|sec|min).*",
        footer=("chatgpt can make mistakes", "chatgpt muze delat chyby"),
        end_labels=["copy", "read aloud", "more actions"],                    # icon descs
        modes={"search": ["search", "web search", "search the web", "hledat"],  # absent
               "thinking": ["thinking", "think harder"],                     # popup only
               "deep_research": ["deep research", "hloubkovy vyzkum"]},      # popup only
        top=270,                   # header buttons span y 142-262 (portrait and landscape)
    ),
    "perplexity": _profile(
        # Calibrated on Perplexity 2.105.0 (English UI, logged in, free plan), 2026-11-25, on
        # one real review request (4.2k-character prompt, default Search mode and model).
        # SHARING SUBMITS AT ONCE: ACTION_SEND text/plain to MainActivity (its only target)
        # opens a new thread and runs the search immediately, with no chance to check the
        # prompt, pick a mode or stop before sending. So the prompt is pasted instead: the
        # composer is EditText#input-search (hint "Ask anything…" is a separate TextView) and
        # KEYCODE_PASTE fills it; Send is View#send-button (desc "Send").
        # The keyboard left up by pasting hides the composer's buttons: "+" (desc "Source input
        # and search mode selector") opens an "Options" sheet with Camera/Image/File,
        # "Connectors & Sources" and Modes: Deep research (View#search-mode-item-research),
        # Model council (Max plan), Computer (agent; checkable). There is no Pro toggle in
        # 2.105; the model chip ("Model selector", e.g. "Sonnet 5.5 - Thinking") is the plan's
        # default. Plain mode = no mode picked.
        # In a thread the header is Close (View#thread-close-button, back to the home screen
        # with the new composer) and the tabs Perplexity / Sources / Images. A long prompt's
        # echo fills the screen and there is no jump-to-bottom button, so the answer is read
        # with reply_via="scroll". A "This question looks difficult ... Upgrade to Pro" card
        # floats over the lower half and swallows swipes that start on it: drags stay above it.
        # The answer is TextViews (headings and paragraphs; trailing spaces where citation
        # chips sit), with inline chips (TextView "github +1" inside a View whose desc is
        # "[github +1]"). Under a finished answer: "Prepared using <model>", then the icon row
        # Share / Read out loud / Rewrite / "15 sources" (Button) / More options, then
        # View#thread-related-item follow-ups. The Sources tab lists View#thread-source-item
        # cards: site ("github", "nvd.nist"), title, snippet - no URLs.
        # The phone auto-rotated to landscape mid-run (x up to 2400); everything above holds.
        pkg="ai.perplexity.app.android",
        share="ai.perplexity.app.android/.ui.main.MainActivity",
        text_entry="paste",
        paste_via="key",
        composer="input-search",
        hide_keyboard=True,
        reply_via="scroll",
        send_labels=["send", "submit", "odeslat"],
        stop_labels=["stop", "stop generating", "zastavit"],                  # not seen yet
        new_chat_labels=["close", "new thread"],
        mode_menu_labels=["source input and search mode selector"],
        login_labels=["sign up", "log in", "continue with google", "continue with apple"],
        chrome={"perplexity", "sources", "images", "ask anything…", "ask anything...",
                "ask anything", "this question looks difficult", "upgrade to pro",
                "upgrade plan"},
        chrome_re=r"try a more powerful model.*",
        footer=("prepared using ",),
        end_labels=["read out loud", "rewrite"],                              # icon descs
        end_ids=["thread-related-item", "thread-sources-action"],
        modes={"research": ["deep research", "research", "hloubkovy vyzkum"],
               "pro": ["pro search"]},                                   # absent in 2.105
        top=230,                   # header (Close, tabs, more actions) is centred at y=145
    ),
}

ALIASES = {
    "deep seek": "deepseek", "moonshot": "kimi", "kimi chat": "kimi",
    "x": "grok", "xai": "grok", "x ai": "grok",
    "chat gpt": "chatgpt", "openai": "chatgpt", "open ai": "chatgpt", "gpt": "chatgpt",
    "pplx": "perplexity",
}
SELECTED_HINTS = ("selected", "vybrano", "zapnuto", "enabled", "on")
DOMAIN = re.compile(r"^(https?://\S+|(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/\S*)?)"
                    r"(?:\s*\+\d+)?$", re.I)


# ------------------------------------------------------------------ resolving
def resolve_app(name: str) -> str:
    n = norm(name or "")
    for cand in (n, n.replace(" ", "")):
        if cand in PROFILES:
            return cand
        if cand in ALIASES:
            return ALIASES[cand]
    raise ChatbotError(f"unknown chat app {name!r}; use one of: " + ", ".join(APPS))


def resolve_mode(app: str, mode: str) -> str:
    app = resolve_app(app)
    modes = PROFILES[app]["modes"]
    m = norm(mode or "").lstrip("+").strip()
    if not m:
        return ""
    for cand in (m, m.replace(" ", "_"), m.replace("_", " ")):
        if cand in modes:
            return cand
    flat = m.replace("_", " ")
    for key, labels in modes.items():
        if flat in labels or flat.replace(" ", "") in (l.replace(" ", "") for l in labels):
            return key
    raise ChatbotError(f"unknown {app} mode {mode!r}; use one of: " + ", ".join(modes))


# ------------------------------------------------------------------ reading
def _own(app: str, ns: list[dict]) -> list[dict]:
    pkg = PROFILES[app]["pkg"]
    return [n for n in ns if n.get("pkg") == pkg]


def in_app(app: str, ns: list[dict]) -> bool:
    return bool(_own(resolve_app(app), ns))


def composer(app: str, ns: list[dict]) -> dict | None:
    app = resolve_app(app)
    hint = PROFILES[app]["composer"]
    for n in _own(app, ns):
        if n.get("edit") and (not hint or hint in (n.get("id") or "")):
            return n
    return None


def _holds(c: dict | None, prompt: str) -> bool:
    return c is not None and bool(norm(prompt)) and norm(c.get("text") or "").startswith(norm(prompt)[:10])


def is_generating(app: str, ns: list[dict]) -> bool:
    app = resolve_app(app)
    return find(_own(app, ns), PROFILES[app]["stop_labels"], exact=True) is not None


def _is_source(n: dict) -> bool:
    t = (n.get("text") or "").strip()
    return bool(t) and (bool(DOMAIN.match(t)) or any(w in (n.get("id") or "").lower()
                                                     for w in ("source", "citation")))


CHIP = re.compile(r"^\[(.+)\]$")       # Perplexity wraps an inline citation chip's text in a
SITE = re.compile(r"^[\w.-]+$")         # View whose desc is "[github +1]"; a source card's
                                         # first text is its site ("github", "nvd.nist")


def _chips(ns: list[dict]) -> set[str]:
    """Texts of Perplexity's inline citation chips ("github +1", "marked.js") on this screen."""
    return {m.group(1).strip() for n in ns if (m := CHIP.match((n.get("desc") or "").strip()))}


def _source_cards(ns: list[dict]) -> list[str]:
    """Perplexity's Sources tab: each View#thread-source-item is followed by its site, title
    and snippet TextViews. Gives "site: title"; a card cut off so that its site or title is
    missing is skipped (it is read whole on the next page)."""
    out: list[str] = []
    card: list[str] | None = None
    for n in _own("perplexity", ns) + [{"id": "thread-source-item"}]:
        if n.get("id") == "thread-source-item" or n.get("id") == "thread-close-button":
            if card and len(card) >= 2 and SITE.match(card[0]):
                s = f"{card[0]}: {card[1]}"
                if s not in out:
                    out.append(s)
            card = [] if n.get("id") == "thread-source-item" else None
        elif card is not None and (n.get("text") or "").strip():
            card.append(n["text"].strip())
    return out


def sources(ns: list[dict]) -> list[str]:
    """Perplexity citations, in screen order without duplicates: on the Sources tab the cards
    ("github: Releases · markedjs/marked"); on the answer, the inline chips ("github",
    "nvd.nist"; '+2' counters dropped) and any link whose text looks like a domain or URL."""
    cards = _source_cards(ns)
    if cards:
        return cards
    chips = _chips(ns)
    out: list[str] = []
    for n in _own("perplexity", ns):
        t = (n.get("text") or "").strip()
        if t and (t in chips or _is_source(n)):
            t = re.sub(r"\s*\+\d+$", "", t)
            if t not in out:
                out.append(t)
    return out


END = None                     # marker for an end heading in _chat_items
BULLETS = {"•", "◦", "▪", "‣", "-", "*"}


def _is_end(p: dict, n: dict) -> bool:
    return (label(n) in [norm(e) for e in p["end_labels"]]
            or any(e in (n.get("id") or "") for e in p["end_ids"]))


def _chat_items(app: str, ns: list[dict]) -> list[str | None]:
    """Text nodes below the header, minus composer, buttons, chrome, footers and perplexity
    source chips, in screen order; an end heading becomes END. A lone bullet node (Grok draws
    list bullets as separate TextViews) is joined to the text after it."""
    p = PROFILES[app]
    ui = set(p["chrome"]) | COMMON_CHROME
    ui |= {norm(l) for key in ("send_labels", "stop_labels", "new_chat_labels", "jump_labels")
           for l in p[key]}
    pat = re.compile(p["chrome_re"]) if p["chrome_re"] else None
    chips = _chips(ns) if app == "perplexity" else set()
    out: list[str | None] = []
    bullet = ""
    for n in _own(app, ns):
        if n.get("y", 0) <= p["top"]:
            continue
        if _is_end(p, n):
            out.append(END)
            bullet = ""
            continue
        t = n.get("text") or ""
        if not t or n.get("edit") or n.get("cls") == "Button":
            continue
        k = norm(t)
        if k in ui or k.startswith(tuple(p["footer"])) or (pat and pat.fullmatch(k)):
            continue
        if app == "perplexity" and (_is_source(n) or t.strip() in chips):
            continue
        if t.strip() in BULLETS:
            bullet = t.strip() + " "
            continue
        out.append(bullet + t.rstrip())
        bullet = ""
    return out


def _until_end(items: list[str | None]) -> str:
    return "\n".join(items[:items.index(END)] if END in items else items).strip()


def chat_text(app: str, ns: list[dict]) -> str:
    """Visible chat text of the app, up to the first end heading."""
    return _until_end(_chat_items(resolve_app(app), ns))


def reply_text(app: str, ns: list[dict], prompt: str) -> str:
    """Chat text after the last echo of the prompt, up to the next end heading (all of it if
    the echo scrolled away). The echo is matched per node first, since one node can hold a
    whole multi-line prompt (Grok), then per line."""
    items = _chat_items(resolve_app(app), ns)
    head = norm(prompt)[:30]
    for i in range(len(items) - 1, -1, -1):
        if head and items[i] is not END and norm(items[i]).startswith(head):
            return _until_end(items[i + 1:])
    text = _until_end(items)
    lines = text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if head and norm(lines[i]).startswith(head):
            return "\n".join(lines[i + 1:]).strip()
    return text


def copy_button(app: str, ns: list[dict]) -> dict | None:
    """The lowest Copy button below the header: the newest answer's (reply_via="copy")."""
    p = PROFILES[resolve_app(app)]
    hits = [n for n in _own(app, ns) if n.get("y", 0) > p["top"] and label(n) in p["copy_labels"]]
    return hits[-1] if hits else None


def _strip_chrome_head(app: str, text: str) -> str:
    """Drop leading lines that are UI ("Worked for 45s") from copied answer text."""
    p = PROFILES[app]
    pat = re.compile(p["chrome_re"]) if p["chrome_re"] else None
    lines = text.strip().split("\n")
    while lines and (not lines[0].strip() or norm(lines[0]) in p["chrome"]
                     or (pat and pat.fullmatch(norm(lines[0])))):
        lines.pop(0)
    return "\n".join(lines).strip()


def copy_reply(app: str, ns: list[dict]) -> str:
    """reply_via="copy": tap the newest answer's Copy button, paste the clipboard into the
    (empty) composer, read it there and clear the composer again. Refuses to touch a composer
    that holds a draft; raises if the composer cannot be cleared, so nothing is left to send."""
    app = resolve_app(app)
    b = copy_button(app, ns)
    if b is None:
        raise ChatbotError(f"no Copy button under the {app} answer")
    c = composer(app, ns)
    if c is None:
        raise ChatbotError(f"{app} shows no composer to paste the copied answer into")
    if norm(c.get("text") or ""):
        raise ChatbotError(f"the {app} composer holds a draft; not pasting over it")
    tap(*clickable_at(ns, b))
    time.sleep(1.0)
    tap(c["x"], c["y"])
    time.sleep(1.0)
    _droid("sh", "input keyevent 279")                 # KEYCODE_PASTE
    time.sleep(1.5)
    text = (composer(app, nodes()) or {}).get("text") or ""
    for _ in range(2):
        _droid("sh", CLEAR_KEYS)
        time.sleep(0.5)
        left = composer(app, nodes())
        if left is None or not norm(left.get("text") or ""):
            break
    else:
        raise ChatbotError(f"could not clear the copied answer from the {app} composer; "
                           "clear it by hand before sending anything")
    if not text.strip():
        raise ChatbotError(f"pasting into the {app} composer gave no text")
    return _strip_chrome_head(app, text)


def _ends_shown(app: str, ns: list[dict]) -> bool:
    """An end marker (the action row or follow-ups under a finished answer) is on screen."""
    p = PROFILES[app]
    return any(_is_end(p, n) for n in _own(app, ns) if n.get("y", 0) > p["top"])


def _drag(app: str, ns: list[dict], up: bool, times: int = 1, ms: int = 700) -> None:
    """Drag the chat between top+40 and top+scroll_span, at the horizontal middle of the app's
    nodes (works in portrait and landscape). up=True moves towards the newest text."""
    p = PROFILES[app]
    xs = [n["x"] for n in _own(app, ns)] or [540]
    x = (min(xs) + max(xs)) // 2
    y_hi, y_lo = p["top"] + p["scroll_span"], p["top"] + 40
    a, b = (y_hi, y_lo) if up else (y_lo, y_hi)
    _droid("sh", "; ".join([f"input swipe {x} {a} {x} {b} {ms}"] * times))


def _merge_above(page: list[str], acc: list[str]) -> list[str]:
    """Join a page read higher up the chat onto the text read so far, dropping the overlap."""
    for k in range(min(len(page), len(acc)), 0, -1):
        if page[-k:] == acc[:k]:
            return page + acc[k:]
    return page + acc


def scroll_reply(app: str, prompt: str, ns: list[dict], pages: int = 30) -> str:
    """reply_via="scroll": fling to the end of the chat, then drag back up a part of a screen
    at a time, merging what each page shows, until the prompt echo (or the top) is reached.
    Gives the text between the last echo and the first end label below it."""
    app = resolve_app(app)
    head = norm(prompt)[:30]
    _drag(app, ns, up=True, times=6, ms=80)
    time.sleep(1.5)
    acc: list[str] | None = None
    last: list | None = None
    for _ in range(pages):
        ns = nodes()
        items = _chat_items(app, ns)
        if acc is not None and items == last:
            break                                       # did not move: top of the chat
        last = items
        page = items[:items.index(END)] if END in items else items
        echo = [i for i, t in enumerate(page) if head and norm(t).startswith(head)]
        if echo:
            page = page[echo[-1] + 1:]
        acc = page if acc is None else _merge_above(page, acc)
        if echo:
            break
        _drag(app, ns, up=False)
        time.sleep(1.0)
    return "\n".join(acc or []).strip()


def read_sources(app: str, ns: list[dict], pages: int = 12) -> list[str]:
    """Perplexity: open the thread's Sources tab, read every source card (dragging down until
    nothing new shows), then go back to the answer tab. Falls back to the inline chips."""
    tab = find(_own(app, ns), ["sources"], exact=True)
    if tab is None or tab.get("y", 0) > PROFILES[app]["top"]:
        return sources(ns)                              # no Sources tab in the header
    tap(*clickable_at(ns, tab))
    time.sleep(2.0)
    out: list[str] = []
    same = 0
    for _ in range(pages):
        ns = nodes()
        new = [s for s in sources(ns) if s not in out]
        out += new
        same = 0 if new else same + 1
        if same >= 2:
            break
        _drag(app, ns, up=True)
        time.sleep(1.0)
    back = find(_own(app, ns), ["perplexity"], exact=True)
    if back is not None:
        tap(*clickable_at(ns, back))
        time.sleep(1.0)
    return out or sources(ns)


def reply(app: str) -> dict | str:
    """The answer currently on screen in `app`; an error string if the app is not in front.
    For reply_via="copy" apps, the newest answer is copied when its Copy button is shown."""
    try:
        app = resolve_app(app)
        ns = nodes()
    except gemini.GeminiError as e:
        return str(e)
    if not in_app(app, ns):
        return f"{app} is not on screen; open it (or ask it something) first"
    gen = is_generating(app, ns)
    text = chat_text(app, ns)
    via = PROFILES[app]["reply_via"]
    try:
        if via == "copy" and not gen and copy_button(app, ns) is not None:
            text = copy_reply(app, ns)
        elif via == "scroll" and not gen:
            text = scroll_reply(app, "", ns)            # no prompt: up to the top of the chat
            ns = nodes()
    except gemini.GeminiError as e:
        return str(e)
    out = {"app": app, "reply": text, "generating": gen}
    if app == "perplexity":
        out["sources"] = read_sources(app, ns) if via == "scroll" and not gen else sources(ns)
    return out


# ------------------------------------------------------------------ steps
def _ready(app: str, ns: list[dict], prompt: str) -> bool:
    """The prompt sits in the composer; for a hidden composer, the send button is shown."""
    if PROFILES[app]["composer_hidden"]:
        return find(_own(app, ns), PROFILES[app]["send_labels"], exact=True) is not None
    return _holds(composer(app, ns), prompt)


def share_prompt(app: str, prompt: str) -> list[dict]:
    q = prompt.replace("'", "'\\''")
    _droid("sh", f"am start -a android.intent.action.SEND -t text/plain "
                 f"--es android.intent.extra.TEXT '{q}' -n {PROFILES[app]['share']}")
    if PROFILES[app]["composer_hidden"]:
        time.sleep(2.0)            # do not take the app's previous screen for the shared one
    return wait_for(lambda ns: _ready(app, ns, prompt), timeout=SHARE_WAIT)


def _launch(app: str) -> list[dict]:
    p = PROFILES[app]
    if p["launch"]:
        _droid("sh", f"am start -n {p['launch']}")
    else:
        _droid("sh", f"monkey -p {p['pkg']} -c android.intent.category.LAUNCHER 1")
    return wait_for(lambda ns: composer(app, ns) is not None, timeout=SHARE_WAIT)


def paste_prompt(app: str, prompt: str, log=print) -> list[dict]:
    """Clipboard route: copy via the share sheet, open the app, new chat, long-press -> Paste."""
    notebook.to_clipboard(prompt)
    ns = nodes()
    if not in_app(app, ns):
        ns = _launch(app)
    c = composer(app, ns)
    if c is None or (norm(c.get("text") or "") and not _holds(c, prompt)):
        b = find(_own(app, ns), PROFILES[app]["new_chat_labels"])
        if b is not None:
            tap(*clickable_at(ns, b))
            time.sleep(1.5)
            ns = nodes()
            log("new chat")
    c = composer(app, ns)
    if c is None:
        raise ChatbotError(f"{app} shows no composer; screen: "
                           + "; ".join(label(n) for n in ns[:12]))
    tap(c["x"], c["y"])
    time.sleep(1.0)
    _droid("sh", CLEAR_KEYS)
    time.sleep(0.5)
    if PROFILES[app]["paste_via"] == "key":
        for attempt in range(2):   # right after the share sheet closes, the first tap can be
            _droid("sh", "input keyevent 279")             # lost and the field is unfocused
            time.sleep(1.5)
            ns = nodes()
            c = composer(app, ns)
            if _holds(c, prompt) or attempt or c is None:
                break
            tap(c["x"], c["y"])
            time.sleep(1.0)
            _droid("sh", CLEAR_KEYS)
            time.sleep(0.5)
        if not _holds(c, prompt):
            raise ChatbotError(f"Paste key left the {app} composer without the prompt; it shows "
                               f"{(c or {}).get('text', '')[:60]!r}")
        return ns
    c = composer(app, nodes()) or c
    _droid("longtap", str(c["x"]), str(c["y"]))
    time.sleep(1.2)
    ns = nodes()
    paste = find(ns, PASTE_LABELS, exact=True)
    if paste is None:
        raise ChatbotError(f"no Paste menu on the {app} composer")
    tap(*clickable_at(ns, paste))
    time.sleep(1.0)
    ns = nodes()
    c = composer(app, ns)
    if not _holds(c, prompt):
        raise ChatbotError(f"the {app} composer does not hold the prompt; it shows "
                           f"{(c or {}).get('text', '')[:60]!r}")
    return ns


def _check_login(app: str, ns: list[dict]) -> None:
    if find(_own(app, ns), PROFILES[app]["login_labels"], exact=True) is not None:
        raise ChatbotError(f"{app} shows its login screen; log in to the app on the phone first")


def open_chat(app: str, prompt: str, log=print) -> list[dict]:
    if PROFILES[app]["text_entry"] != "paste":
        ns = share_prompt(app, prompt)
        if _ready(app, ns, prompt):
            log("shared, prompt in composer" if not PROFILES[app]["composer_hidden"]
                else "shared (composer not readable; send button shown)")
            return ns
        if PROFILES[app]["composer_hidden"]:
            raise ChatbotError(f"sharing to {app} showed no send button, and its composer "
                               "cannot be read, so pasting cannot be verified either")
        log("share did not fill the composer; pasting")
    if PROFILES[app]["login_labels"]:
        _check_login(app, nodes())
    ns = paste_prompt(app, prompt, log)
    log("pasted, prompt in composer")
    return ns


def _mode_state(app: str, key: str, ns: list[dict]) -> bool | None:
    """True/False when a node of the mode's toggle carries a checked/selected hint, else None."""
    for n in _own(app, ns):
        if find([n], PROFILES[app]["modes"][key]) is None:
            continue
        # the state may sit on the label node or on its clickable parent (DeepSeek Think/Search)
        near = [n] + [m for m in reversed(ns[: n.get("i", 0)])
                      if "checked" in m and abs(m["y"] - n["y"]) < 90 and abs(m["x"] - n["x"]) < 300][:1]
        for m in near:
            for k in ("checked", "selected"):
                if k in m:
                    return bool(m[k])
        d = norm(n.get("desc") or "")
        if d and any(re.search(rf"\b{h}\b", d) for h in SELECTED_HINTS) and d != label(n):
            return True
    return None


def pick_mode(app: str, key: str, ns: list[dict], log=print) -> list[dict]:
    p = PROFILES[app]
    labels = p["modes"][key]
    if _mode_state(app, key, ns) is True:
        log(f"mode {key} already on")
        return ns
    t = find(_own(app, ns), labels)
    if (t is None and p["hide_keyboard"]
            and find(_own(app, ns), p["mode_menu_labels"] or labels) is None):
        _droid("key", "back")      # the keyboard hides the composer's buttons (Perplexity)
        time.sleep(1.0)
        ns = nodes()
        t = find(_own(app, ns), labels)
    if t is None and p["mode_menu_labels"]:
        menu = find(_own(app, ns), p["mode_menu_labels"])
        if menu is not None:
            tap(*clickable_at(ns, menu))
            ns = wait_for(lambda ns: find(_own(app, ns), labels) is not None, timeout=6)
            t = find(_own(app, ns), labels)
            if t is None:
                _droid("key", "back")    # do not leave the menu open over the composer
    if t is None:
        raise ChatbotError(f"no {key} toggle on the {app} screen; it shows: "
                           + ", ".join(label(n) for n in _own(app, ns) if label(n))[:400])
    tap(*clickable_at(ns, t))
    time.sleep(1.5)
    ns = nodes()
    state = _mode_state(app, key, ns)
    log(f"mode {key}: " + {True: "on", False: "NOT on", None: "tapped (state not visible)"}[state])
    return ns


def send(app: str, ns: list[dict]) -> list[dict]:
    b = find(_own(app, ns), PROFILES[app]["send_labels"], exact=True)
    if b is None:
        raise ChatbotError(f"no send button in the {app} composer")
    tap(*((b["x"], b["y"]) if PROFILES[app]["send_tap"] == "self" else clickable_at(ns, b)))
    time.sleep(2.0)
    ns = nodes()
    _check_login(app, ns)
    # Text that arrived by share intent may need a confirmation; the user asked for this prompt
    # to go to this app, so confirm (same as gemini.send).
    ok = gemini.share_consent(ns)
    if ok is not None:
        tap(*clickable_at(ns, ok))
        time.sleep(2.0)
        ns = nodes()
    return ns


def _scroll_to_end(app: str, ns: list[dict]) -> None:
    """Fling the chat up (towards the newest text) a few times, between composer and header."""
    c = composer(app, ns)
    if c is None:
        return
    y0, y1 = c["y"] - 250, PROFILES[app]["top"] + 150
    if y0 > y1:
        _droid("sh", "; ".join([f"input swipe {c['x']} {y0} {c['x']} {y1} 150"] * 3))


def wait_reply(app: str, prompt: str, wait_s: int, step: float = 3.0) -> list[dict]:
    """Poll until nothing is generating and the reply is unchanged between two reads
    (reply_via="copy": until nothing is generating and the answer's Copy button is shown,
    flinging the chat towards its end when it is not). A failed uiautomator dump counts as
    still generating: ChatGPT's screen is never idle while it streams."""
    end, last = time.time() + wait_s, None
    jump = PROFILES[app]["jump_labels"]
    via_copy = PROFILES[app]["reply_via"] == "copy"
    ns: list[dict] = []
    while True:
        try:
            ns = nodes()
        except gemini.GeminiError:
            if time.time() >= end:
                raise
            last = None
            time.sleep(step)
            continue
        b = find(_own(app, ns), jump, exact=True) if jump else None
        if b is not None:          # the chat stayed on the prompt echo: go to the newest text
            tap(*clickable_at(ns, b))
            time.sleep(1.0)
            try:
                ns = nodes()
            except gemini.GeminiError:
                pass               # keep the screen read before the tap
        gen = is_generating(app, ns)
        if PROFILES[app]["reply_via"] == "scroll":
            if (not gen and _ends_shown(app, ns)) or time.time() >= end:
                return ns
            _drag(app, ns, up=True, times=6, ms=80)    # the answer grows below the echo
            time.sleep(step)
            continue
        if via_copy:
            if not gen and copy_button(app, ns) is not None:
                return ns
            if time.time() >= end:
                return ns
            if not gen:
                _scroll_to_end(app, ns)
            time.sleep(step)
            continue
        cur = reply_text(app, ns, prompt)
        if (cur and not gen and cur == last) or time.time() >= end:
            return ns
        last = None if gen else cur
        time.sleep(step)


def _dump(d: str | None, name: str, ns: list[dict]) -> None:
    if d:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as f:
            json.dump(ns, f, ensure_ascii=False, indent=0)


def ask(app: str, prompt: str, mode: str = "", send_it: bool = True, wait_s: int = 0,
        log=print, dump: str | None = None) -> dict:
    app = resolve_app(app)
    prompt = (prompt or "").strip()
    if not prompt:
        raise ChatbotError("prompt is empty")
    if len(prompt) > PROMPT_MAX:
        raise ChatbotError(f"prompt is {len(prompt)} characters; limit {PROMPT_MAX}")
    key = resolve_mode(app, mode)
    ns = open_chat(app, prompt, log)
    _dump(dump, "01-shared.json", ns)
    if key:
        ns = pick_mode(app, key, ns, log)
        _dump(dump, "02-mode.json", ns)
    out = {"app": app, "mode": key, "sent": False, "reply": ""}
    if not send_it:
        return out
    ns = send(app, ns)
    out["sent"] = True
    log("sent")
    _dump(dump, "03-sent.json", ns)
    if wait_s:
        ns = wait_reply(app, prompt, wait_s)
    _dump(dump, "04-reply.json", ns)
    if (PROFILES[app]["reply_via"] == "copy" and not is_generating(app, ns)
            and copy_button(app, ns) is not None):
        out["reply"] = copy_reply(app, ns)
        return out
    if PROFILES[app]["reply_via"] == "scroll" and not is_generating(app, ns) \
            and _ends_shown(app, ns):
        out["reply"] = scroll_reply(app, prompt, ns)
        ns = nodes()
        if app == "perplexity":
            out["sources"] = read_sources(app, ns)
        return out
    out["reply"] = reply_text(app, ns, prompt)
    if app == "perplexity":
        out["sources"] = sources(ns)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Ask a chat app on the phone")
    ap.add_argument("app", help="one of: " + ", ".join(APPS))
    ap.add_argument("prompt")
    ap.add_argument("--mode", default="")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--wait", type=int, default=0)
    ap.add_argument("--dump", default=None, help="save each step's node list as JSON here")
    a = ap.parse_args(argv)
    try:
        r = ask(a.app, a.prompt, a.mode, send_it=not a.no_send, wait_s=a.wait,
                log=lambda m: print("..", m, file=sys.stderr), dump=a.dump)
    except gemini.GeminiError as e:
        print("error:", e, file=sys.stderr)
        return 1
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
