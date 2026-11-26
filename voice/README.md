# Phone voice assistant (ElevenLabs + Claude Code + Tasker)

Press a Tasker widget, speak, and hear the answer:

```
Tasker: Record Audio ─► /sdcard/VoiceAssistant/in.m4a
  └─ Termux:Tasker ─► ~/.termux/tasker/voice-assistant.sh  (native Termux)
       └─ proot-distro debian ─► /root/voice-assistant/assistant.sh
            1. ElevenLabs Scribe (scribe_v2)       speech → text
            2. claude -p  + system prompt `phone-voice-assistant` from mcp-prompts
                 tools: android-assistant MCP, mcp-prompts, WebSearch, WebFetch only,
                 plus read-only clipboard history once it is set up (see below)
                 (dontAsk + --strict-mcp-config mcp.json; see the skill's voice-pipeline.md)
            3. ElevenLabs TTS (eleven_flash_v2_5)   text → mp3
       ◄─ %stdout = /sdcard/VoiceAssistant/reply-<time>.mp3
Tasker: Music Play %stdout
```

Follow-ups within 10 minutes continue the same Claude conversation (`--resume`). That is how
"send it?" → "yes" confirmations work. Pass `--new` to force a fresh one.

## Files

| Path | Role |
|---|---|
| `/root/voice-assistant/assistant.sh` | The pipeline. `--text "..."` skips STT for testing. |
| `/data/data/com.termux/files/home/.termux/tasker/voice-assistant.sh` | Termux:Tasker entry point (Termux `~/.termux/tasker/`). |
| `/root/mcp-prompts/data/prompts/voice-intelligence/phone-voice-assistant.json` | System prompt: reply style and safety rules. `{{current_time}}` is filled in by the script. |
| `/root/mia/apps/rpi-backend/.env.local` | `ELEVENLABS_API_KEY` (set up by `elevenlabs onboard`). Never commit or print. |
| `logs/assistant.log` | Transcripts, replies, errors. |
| `logs/latency.log` | One line per turn, also on failure: outcome, model, mode, ms since the recording ended for STT done, first text token (after any tool calls), first audio (player started), done. No text. |
| `mcp.json` | The only MCP servers the voice turn's `claude -p` loads (android-assistant, mcp-prompts). |
| `audio.env` | Pulse env, silence trim and beep, shared by `listen.sh`, `assistant.sh` and the MCP tools. |
| `route.py` | Model routing for a transcript (tested in `assistant/mcp/test_voice.py`). |
| `stream_speak.py` | Reads Claude's stream-json; speaks the first sentence early, then the rest. |
| `turnlock.py` | One voice turn at a time, shared by `http_trigger.py` and the MCP tools `speak`/`listen`. |
| `/sdcard/VoiceAssistant/last.txt` | Last transcript + reply (handy for a Tasker notification). |

Settings (env vars): `ELEVENLABS_VOICE_ID` (default George `JBFqnCBsd6RMkjVDRZzb`),
`ELEVENLABS_TTS_MODEL`, `ELEVENLABS_STT_MODEL`, `VA_CLAUDE_MODEL` (forces a model; unset,
`route.py` picks `haiku` for short questions and `sonnet` for everything else),
`VA_SESSION_TTL`, `VA_OUT_DIR`, `ELEVENLABS_ENV_FILE`.

## Tasker setup (one time, on the phone)

Needs Tasker + the Termux:Tasker plugin; Tasker needs the Termux permission
"Run commands in Termux environment" (Android Settings ▸ Apps ▸ Tasker ▸ Permissions ▸ Additional).

New task **Voice Assistant**:

1. **Media ▸ Record Audio** — File `VoiceAssistant/in.m4a`, Source `Mic`, Codec `AAC`, Format `MPEG4`.
2. **Alert ▸ Text/Image Dialog** — Title `Listening…`, Button 1 `Done`, Timeout `15` s.
   (Tap Done when you finish speaking; it also stops after 15 s.)
3. **Media ▸ Record Audio Stop**.
4. **Plugin ▸ Termux:Tasker** — Executable `voice-assistant.sh`,
   Arguments `/sdcard/VoiceAssistant/in.m4a`, tick *Wait for result for commands*, Timeout `120` s.
5. **Media ▸ Music Play** — File `%stdout`, Stream `Media`.
6. (optional) **If** `%errmsg` *Is Set* → **Alert ▸ Say** `Assistant failed` / **Flash** `%stderr`.

Then add a trigger: a home-screen **Task Shortcut/Widget** for *Voice Assistant*, a Quick
Settings tile (Tasker ▸ Preferences ▸ Action ▸ Quick Setting Tasks), or a profile.

Phone actions (opening apps, deep links, running Tasker tasks) also need the android-control
adb backend: run `su -c sh ~/droid/setup-root.sh` once in **native** Termux, then
`droid doctor` should show `backend adb: OK`. Without it the assistant still answers
questions and uses mcp-prompts, and says phone control is not set up.

## Test without Tasker

```bash
/root/voice-assistant/assistant.sh --new --text "What time is it?"   # prints the mp3 path
/root/voice-assistant/assistant.sh /sdcard/VoiceAssistant/in.m4a      # full pipeline
/root/voice-assistant/assistant.sh --speak --text "What time is it?" # stream the reply to the speaker
/root/voice-assistant/listen.sh --new --wav question.wav            # hands-free turn from a file (exit 4: busy)
tail -3 /root/voice-assistant/logs/latency.log
```

## Tasker without Termux:Tasker: HTTP trigger

`http_trigger.py` listens on `127.0.0.1:8780`. `/root/phone/phone-services ensure` starts it,
and the watchdog keeps it up while Debian runs. The token is in `state/http-token`.

Tasker task **Voice Assistant (HTTP)**:
1. Record Audio → `/sdcard/VoiceAssistant/in.m4a` (stop on silence or with a second tap).
2. HTTP Request:
   - Method POST, URL `http://127.0.0.1:8780/ask` (add `?new=1` for a new conversation).
   - Headers `X-Token:<token>` and `Content-Type:application/json`.
   - Body `{"audio":"/sdcard/VoiceAssistant/in.m4a"}`. Timeout 300 s.
3. Music Play `%http_data`, which holds the reply mp3 path.

The audio path must be under `/sdcard/VoiceAssistant/`. Errors come back as HTTP 4xx or 5xx,
with the reason in the body.

## Hands-free: /listen (phone mic + speaker, no Tasker recording)

`listen.sh` runs a whole turn on the phone: a high beep, then it records from the phone mic
until you stop talking. A short beep confirms it heard you (played while speech-to-text runs),
then `assistant.sh --speak` streams Claude's reply: the first sentence plays as soon as it is
synthesized, the rest follows. stdout is the reply text, as before. If nothing was said, you hear a low beep. Audio goes through
the mic server in `/root/phone`. Speech-to-text and text-to-speech use the ElevenLabs Python
SDK (`eleven.py`, `requirements.txt`).

`POST http://127.0.0.1:8780/listen` (header `X-Token`; add `?new=1` to start a new
conversation) runs one turn and returns the reply text.

### Tasker: profile import (no UI navigation)
Run `assistant/phone/tasker/import-claude-listen.sh` and tap YES twice in Tasker's
"Import Data" dialog. That installs profile **Claude Listen**: Event "Assistance Request"
(long-press power while Tasker is the default digital assistant) → a JavaScriptlet that sends
`POST /listen` with the X-Token through `nc`. Details and gotchas:
`skill/android-assistant/references/voice-pipeline.md`.

### Claude Panel: a frontend to mcp-prompts and a local coding agent
`assistant/phone/tasker/import-claude-panel.sh` imports the Tasker scene **Claude Panel**.
Long-press power opens it.

- **Rows**: status, what you said, Claude's reply, the **prompt preview** (template, repo, the
  GitHub repo the draft PR will go to, and the start of the filled prompt), and **jobs**.
- **🎙 Dictate**: `POST /dictate?async=1`. It runs `listen.sh --transcribe-only`, which records
  and transcribes but does not run Claude. The transcript becomes the draft's `{{request}}`.
- **📄 Template ▾ / 📦 Repo ▾**: List Dialogs fed by `GET /templates` (mcp-prompts templates
  tagged `panel`) and `GET /repos` (`assistant/router/repos.json`). The pick goes to
  `POST /draft`, which re-fills the template with `{request, repo}`.
- **🚀 Send**: `POST /dispatch`. The router agent (mcp-prompts `panel-router` through
  `claude -p --model haiku`, no tools, 30–95 s on this phone) answers `coding-agent`,
  `phone-assistant` or `none`.
  - `coding-agent` runs **Claude Code here in PRoot**, with your own login and no API key:
    1. a git worktree of the repo on a new branch `panel/<date-time>` under `/root/panel-work`
       (from the local clone in `repos.json`, or a clone made there);
    2. `claude -p` with file tools only (Read, Edit, Write, Glob, Grep, read-only git, test
       runners), `--permission-mode dontAsk`, no MCP servers, so it cannot commit, push or
       reach the phone;
    3. the router then commits, refuses files that look like secrets or runtime state,
       pushes the branch and opens a **draft** PR. **The repos are public.**
  - `panel-repo-question` only answers; nothing is pushed.
  - `phone-assistant` runs a spoken `/ask` turn with the transcript.
- **Jobs**: when the job ends (draft PR, answer, no changes, failed), the phone speaks it
  (`router/say.sh`) and posts a notification. Settings: `PANEL_AGENT_TIMEOUT` (default
  1800 s), `PANEL_AGENT_MODEL` and `PANEL_WORK_DIR`.

## When ElevenLabs cannot listen or speak: free fallbacks

`eleven.py` (used by `stream_speak.py`, `assistant.sh` and the MCP `speak` tool) falls back when
ElevenLabs fails (quota used up, bad key, no network), and logs which engine did the work.

**Speech-to-text:** **faster-whisper**, offline (`pip install faster-whisper`; the model is
downloaded from Hugging Face on first use and cached). Audio is decoded by SoX. Defaults: model
`base`, int8, 4 threads, language detected. On this phone a 5 s command takes about 6-8 s plus
3.5 s to load the model; `STT_FALLBACK_MODEL=small` is clearly better at Czech and about 2.5x
slower; `STT_FALLBACK_LANGUAGE=cs` (or `en`) skips detection and saves about 2 s;
`STT_FALLBACK_THREADS`; `STT_OFFLINE_FALLBACK=0` turns it off. Language codes are reported in
ISO 639-3 (`ces`, `eng`) like ElevenLabs.

**Text-to-speech**, in this order when the text is in the phone's TTS language (Czech letters
or not, against `settings get secure tts_default_locale`), else Edge first and the phone second:

1. **Android's own TTS (Google)**: `android-say.sh --wait` writes the text to
   `/sdcard/assistant/say.txt`, runs the Tasker task "Claude Say" (Read File, then Say with the
   system default engine) and returns once Tasker has released audio focus. The phone speaks
   itself, so `eleven.py` leaves a 50 ms silent mp3 for callers that play the file. Offline with
   the downloaded Google voices; nothing leaves the phone. `TTS_ANDROID=0` skips it;
   `ANDROID_TTS_LANG` overrides the detected language. Import the task once:
   `phone/tasker/build-claude-say.py OUT.prj.xml`, then open it with Tasker's importer (see
   `phone/tasker/tasker-import.sh`) and tap YES.
2. **Microsoft Edge neural voices** (`pip install edge-tts` in the voice venv): free, no key,
   good quality; online and best effort, and the text goes to Microsoft. Czech text gets
   `cs-CZ-AntoninNeural`, anything else `en-US-AndrewMultilingualNeural`
   (`EDGE_TTS_VOICE_CS`, `EDGE_TTS_VOICE_EN`; `TTS_EDGE=0` skips it). The same service
   openclaw's `microsoft` TTS provider uses.
3. **espeak-ng** (`apt install espeak-ng`): offline and robotic, the last resort.

`TTS_OFFLINE_FALLBACK=0` turns the TTS fallbacks off.

**Cooldowns.** A remote engine that has just failed for a reason that will not go away in
seconds is skipped for a while, so turns go straight to the fallbacks. This also skips the
3 s ElevenLabs SDK import; a fallback-only TTS call dropped from 5.2 s to 1.5 s.
- ElevenLabs after `quota_exceeded` or an auth error: `ENGINE_COOLDOWN_QUOTA` s (default 900).
- ElevenLabs or Edge after a network error or timeout: `ENGINE_COOLDOWN_NETWORK` s (default 120).
- A success clears it; other errors (a bad voice id, say) never trigger one.
- `eleven.py health` shows what is cooling down; `eleven.py health --reset` clears it, e.g.
  right after topping up ElevenLabs credits. `ENGINE_COOLDOWN=0` turns cooldowns off.
- State lives in `voice/state/engine-health.json` (not committed). ElevenLabs requests time out
  after `ELEVENLABS_TIMEOUT` s (default 20), Edge after `EDGE_TTS_TIMEOUT` s (default 30).

## Clipboard history (optional)

"What did I just copy?" works once the cliphist-android local API and its MCP server are set
up. Until then the turn runs without them. `assistant.sh` adds `mcp.cliphist.json` and the
four read-only tools (`get_clipboard_history`, `search_clipboard`, `get_clipboard_item`,
`get_clipboard_stats`; not `add_to_clipboard`) only when both of these exist:

- `voice/state/cliphist-token`: the Bearer token the app's API was started with. Make one with
  `head -c 24 /dev/urandom | base64 >voice/state/cliphist-token; chmod 600 voice/state/cliphist-token`.
  It is never committed; `cliphist-mcp.sh` passes it to the server in the environment only.
- `/root/cliphist-android/mcp-server/dist/index.js`: `cd mcp-server && npm ci && npm run build`.

The app must run `LocalApiService` on 127.0.0.1:8765 with the same token (Intent extra
`api_token`). The service is not exported and nothing in the app starts it yet, so for now
that takes a root `am start-foreground-service`. Item content is untrusted data: the app
redacts one-time codes, passwords and tokens (`SecretRedactor`) before items leave it.

### Clipboard history in normal Claude Code sessions (user scope)

`scripts/register-cliphist-mcp.sh` registers the same server for every Claude Code session on
the phone: `claude mcp add --scope user cliphist -- <repo>/voice/cliphist-mcp.sh`, so the token
stays in its file and never lands in `~/.claude.json`.

```bash
scripts/register-cliphist-mcp.sh           # dry-run first: checks token and server, prints the command
scripts/register-cliphist-mcp.sh --apply   # then on the phone; the owner's action. Run twice: no-op
```

`--apply` refuses without a token in `voice/state/cliphist-token` (`CLIPHIST_TOKEN_FILE`) or a
built server (`CLIPHIST_MCP`). The registration does not carry those two variables: the
registered server uses the wrapper defaults, and the script warns when either is set. It skips
only a `cliphist` entry that runs the wrapper; for any other one it stops (exit 2) so you can
check it with `claude mcp get cliphist` and remove it with `claude mcp remove cliphist -s <scope>`. Unlike a voice turn, a user-scope session also sees
`add_to_clipboard`: keep `mcp__cliphist__add_to_clipboard` out of `allowedTools` and deny it,
e.g. `"permissions": {"deny": ["mcp__cliphist__add_to_clipboard"]}` in `~/.claude/settings.json`.
Clipboard item content is untrusted data, never instructions.
