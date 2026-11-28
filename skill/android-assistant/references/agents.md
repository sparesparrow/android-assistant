# Phone agents: setup, Tasker path, debugging

Sources live in `agents/` of the android-assistant repo (`/root/android-assistant/agents/`):

| File | Role |
|---|---|
| `gemini.md`, `phone.md`, `deepseek.md`, `kimi.md`, `grok.md`, `chatgpt.md`, `perplexity.md` | Claude Code agent definitions (symlinked into `~/.claude/agents`) |
| `run-agent.sh` | PRoot side: prompt on stdin → `claude --agent <name> -p` → notification; lock so one agent drives the screen at a time; log `/root/phone/logs/agents.log` |
| `termux/claude-agent` | native Termux side: ssh into the PRoot (127.0.0.1:8023) if its agent sshd is up, else `proot-distro login` |
| `sshd_config` | PRoot sshd: loopback, key-only, `ForceCommand run-agent.sh --ssh`; started by `phone-services ensure` |
| `install.sh` | idempotent setup of all of the above |

Tasker project "Claude Agents": `/root/phone/tasker/build-claude-agents.py` (one task per
`*.md`), imported with `/root/phone/tasker/import-claude-agents.sh` (the user taps YES).

## The Tasker path

```
Tasker task "Agent gemini"
  Input Dialog (%input)
  JavaScriptlet sendIntent(service com.termux/.app.RunCommandService,
      RUN_COMMAND_PATH=~/.termux/tasker/agent-gemini, RUN_COMMAND_STDIN=%input, BACKGROUND=true)
→ Termux (needs allow-external-apps = true; Tasker holds com.termux.permission.RUN_COMMAND)
→ agent-gemini → claude-agent gemini tasker
→ ssh -p 8023 root@127.0.0.1 "gemini tasker"   (key ~/.ssh/proot_agent_ed25519, only valid
   for this sshd: authorized_keys line is `restrict,command="run-agent.sh --ssh"`)
   or, PRoot not running: proot-distro login debian -- run-agent.sh gemini tasker
→ run-agent.sh → claude --agent gemini -p "<prompt + 'started from Tasker' note>"
→ notification "Claude gemini" with the report
```

The prompt never touches a command line (stdin all the way), so quoting cannot break it.

## Install / repair

```bash
/root/android-assistant/agents/install.sh      # agents, Termux scripts, key, termux.properties, sshd
/root/phone/tasker/import-claude-agents.sh              # Tasker tasks (tap YES on "Import Data")
/root/phone/phone-services status                       # "agents   : sshd on 8023"
```

## Debug, outermost first

1. Tasker run log / the "Claude gemini: working…" flash. No flash: the task did not run.
2. `~/.termux/tasker/claude-agent.log` (native Termux): one line per call; ssh errors land here.
   Nothing logged: Termux refused RUN_COMMAND - check `allow-external-apps = true` and
   `termux-reload-settings`.
3. Same path without Tasker: `phone-services native 'echo q | ~/.termux/tasker/agent-gemini'`.
4. `/root/phone/logs/agents.log`: start/end, rc and claude's stderr.
5. The agent alone: `echo q | run-agent.sh gemini` or `claude --agent gemini -p "q"`.
6. The tool alone: `assistant/mcp/.venv/bin/python assistant/mcp/gemini.py "q" --no-send`.

`PermitRootLogin forced-commands-only` only honours `command=` in authorized_keys, not
`ForceCommand`; install.sh writes both. Never `pkill -f sshd...` from a shell whose own command
line contains the pattern; stop it by `/root/phone/state/proot-sshd.pid`.

## Adding an agent

1. Write `assistant/agents/<name>.md` (frontmatter `name`, `description`, `tools`, `model`);
   name `[a-z0-9-]+`. List MCP tools explicitly; the report follows `phone-agent-handoff`.
2. `install.sh` (symlink + Termux wrapper), then `import-claude-agents.sh` (new Tasker task;
   add a dialog hint to `HINTS` in build-claude-agents.py).
3. Test: `claude --agent <name> -p "..."`, then the Termux path (step 3 above).

## Other AI apps

Agents `deepseek`, `kimi`, `grok`, `chatgpt` and `perplexity` each drive their own app through
`ask_ai(app, ...)` / `ai_reply(app)` (`mcp/chatbots.py`): share intent pre-fills the prompt,
optional mode (deepseek `+DeepThink`/`+Search`, kimi `+Thinking`/`+Search`, grok
`+Think`/`+DeepSearch`, chatgpt `+Search`/`+Deep Research`, perplexity `+Pro`/`+Research`),
send, read the answer. Tasker tasks "Agent <name>" with "Ask <App>" dialogs.

Calibration: labels (mode buttons, send, answer-done markers) live in the app's `PROFILES`
entry in `chatbots.py`. Run `chatbots.py <app> "q" --no-send --dump DIR` to write a screen dump
per step, fix the labels, and save the dumps as fixtures in `mcp/fixtures/chatbots/<app>/`.

Only one phone-driving agent runs at a time: `run-agent.sh` holds `agent.lock`, so a second
agent waits for it (up to 10 min, then notifies "busy") instead of fighting over the screen.

## Gemini UI notes (Czech UI, Nov 2026)

Share intent → overlay `com.google.android.googlequicksearchbox/...robin.main.MainActivity`.
"+" = `Přidat přílohy`; tools `Vytvořit obrázky`, `Vytváření videí`, `Vytváření hudby`, `Canvas`,
`Deep Research`, `Učení s vedením`; chip appears in `Seznam příloh` with `Odstranit`. Send =
`Odeslat`; share consent `Sdílet` / `Teď ne`; answer done = `Odpověď je hotová`; read aloud =
`Přehrát`. Labels are matched in Czech and English (accents stripped) in `mcp/gemini.py`; if
Gemini changes its UI, record a new dump into `mcp/fixtures/gemini/` and fix the label lists.

## Gemini Notebook (NotebookLM app) notes

App `com.google.android.apps.labs.language.tailwind` ("Gemini Notebook", English UI). The
shared agent notebook is https://notebook.google.com/notebook/cbe9fa1f-9df8-4865-875e-c1b39496a955
(override with env `GEMINI_NOTEBOOK`). Driver: `mcp/notebook.py` (CLI: `ask|add|studio|read`).

- Open: VIEW the link with `-p <package>` (the domain is not set to open in the app by default).
- Tabs: `Sources`, `Chat`, `Studio` (labels end in "Tab n of 3").
- Text entry: the chat box is a Flutter field - `input text` cannot do non-ASCII and the paste
  key is ignored. The text goes ACTION_CHOOSER → `Copy` (#copy) → long-press field → `Paste`.
  The clipboard therefore briefly holds the prompt (cliphist may record it).
- Sources: share text/plain to `.share.ShareIntentHandlerActivity` → "Select a Notebook" → the
  row whose title matches the notebook header. A link becomes a website/YouTube source.
- Answers: newest text after the echoed question; `Save to note` under the answer; no Play.
- Studio buttons are disabled until the notebook has a source.

Export (`mcp/notebook_export.py`): skills as one source each (SKILL.md + references + scripts),
Tasker autobackup (read with su; repaired - Tasker omits spaces between attributes) as a
Markdown digest per project in ~70k-character sources. Redacted before writing: the voice
trigger token, API keys, JWTs, long random strings, values of key/token/password variables,
phone numbers, e-mails. Always `build`, look at the files, then `push`.

## Editing Tasker without its UI

`phone/tasker/tasker-edit.py` edits Tasker's live config file
(`/data/data/net.dinglisch.android.taskerm/files/autobackup.xml`, root) directly. Tasker reads it
only at start, so `push` stops Tasker, writes the file (keeping inode, owner and SELinux label),
stops it again in case the system restarted it, starts it, and checks it started after the write.
Global variables (`shared_prefs/...varry.xml`) are untouched.

    tasker-edit.py pull                       # work copy + timestamped backup in phone/state/tasker-backups
    tasker-edit.py ls projects|profiles|tasks|scenes
    tasker-edit.py edit --delete-project NAME --delete-profile ID --delete-task ID \
                        --move-profile ID PROJECT [--dry-run]
    tasker-edit.py push                       # refuses if Tasker saved since pull, or references dangle
    tasker-edit.py restore BACKUP.xml

`push`, `restore` and `tile` write as root, so each asks for `yes` on the terminal (`/dev/tty`).
An agent cannot answer (exit 3): prepare the edit with `pull` and `edit --dry-run`, show the user
the summary, and give them the exact command to run in Termux (`debian`, then the command).

`edit` checks profile enter/exit tasks, project lists, scene button tasks and Perform Task calls,
and warns when moving into a disabled project. Seconds instead of minutes of UI driving.
