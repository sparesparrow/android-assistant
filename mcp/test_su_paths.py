"""Audit: no Magisk `su` is reachable from the paths a headless agent can drive (mcp-prompts#143).

Agent paths are the android-assistant MCP tools (voice turns, `claude --agent`, Tasker through the
agent sshd) and the agents' tool lists. Root stays in maintenance commands a person runs at a
terminal: tasker-edit.py push/restore/tile (a yes typed on /dev/tty) and phone-services
pa-restart/pa-stop (a tty on stdin, #22). No phone needed.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP = ROOT / "mcp"
# su invocations: `su -c`, a quoted "su" argument, `su <uid|root>`, /system/bin/su, `magisk su`
SU = re.compile(r"""(\bsu -c\b|["']su["']|\bsu (\d+|root)\b|/s?bin/su\b|\bmagisk su\b)""")


def tty_guarded(line: str) -> bool:
    """SU_OK=1 is set only right after a positive tty test ([ -t 0 ] &&), never a negated one."""
    return bool(re.search(r"\[ -t 0 \] && SU_OK=1", line))


def local_closure(start: Path) -> set[Path]:
    """server.py plus every local module it imports (mcp/ and voice/), transitively."""
    seen, todo = set(), [start]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for node in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            for n in names:
                for d in (MCP, ROOT / "voice"):
                    p = d / (n.split(".")[0] + ".py")
                    if p.exists():
                        todo.append(p)
    return seen


def test_mcp_server_modules_never_call_su():
    files = local_closure(MCP / "server.py")
    assert MCP / "gemini.py" in files and MCP / "notebook.py" in files  # the walk does follow imports
    hits = [f"{f.name}:{i}" for f in files for i, line in enumerate(f.read_text().splitlines(), 1)
            if SU.search(line) and not line.lstrip().startswith("#")]
    assert hits == []


def test_mcp_server_runs_phone_services_only_for_status_and_native():
    src = (MCP / "server.py").read_text()
    assert set(re.findall(r"\[PHONE_SERVICES, \"(\w+)\"", src)) == {"status", "native"}


def _branch(script: str, name: str) -> str:
    m = re.search(rf"^  {name}\)(.*?);;", script, re.S | re.M)
    assert m, name
    return m.group(1)


def test_phone_services_uses_su_only_at_a_terminal():
    """#22: ensure, watch, status and native never use su; SU_OK=1 only behind a tty check."""
    script = (ROOT / "phone" / "phone-services").read_text()
    native_fn = re.search(r"^native\(\) \{(.*?)^\}", script, re.S | re.M).group(1)
    for body in (_branch(script, "ensure"), _branch(script, "watch"), _branch(script, "status"), native_fn):
        assert not SU.search(body) and "SU_OK=1" not in body
    setters = [ln for ln in script.splitlines() if "SU_OK=1" in ln and not ln.lstrip().startswith("#")]
    assert setters and all(tty_guarded(ln) for ln in setters)
    lines = script.splitlines()
    su_at = [i for i, ln in enumerate(lines) if SU.search(ln) and not ln.lstrip().startswith("#")]
    assert su_at and all("SU_OK = 1" in lines[i] or "SU_OK = 1" in lines[i - 1] for i in su_at)  # guarded


def test_agents_only_have_assistant_and_prompt_tools():
    for md in sorted((ROOT / "agents").glob("*.md")):
        line = next(ln for ln in md.read_text().splitlines() if ln.startswith("tools:"))
        tools = [t.strip() for t in line[len("tools:"):].split(",")]
        assert tools and all(t.startswith(("mcp__android-assistant__", "mcp__mcp-prompts__")) for t in tools), md.name


def test_voice_turns_disallow_the_shell():
    text = (ROOT / "voice" / "assistant.sh").read_text()
    assert re.search(r"--disallowedTools[^\n]*\bBash\b", text) and "--permission-mode dontAsk" in text


def test_su_pattern_catches_the_common_spellings():
    for s in ['su -c "id"', "'su'", "su 2000 -c x", "/system/bin/su -c id", "su 0 sh", "su root -c id",
              "magisk su -c id", '["su", "-c"]']:
        assert SU.search(s), s
    for s in ["sudo apt", "result", "issue", "su-like", "pa_kill su not tried"]:
        assert not SU.search(s), s


def test_tty_guard_check_rejects_a_negated_test():
    assert tty_guarded('pa-restart) shift; [ -t 0 ] && SU_OK=1 || echo no tty')
    assert not tty_guarded('pa-restart) [ ! -t 0 ] && SU_OK=1')
    assert not tty_guarded("SU_OK=1")
