"""Tests for scripts/register-cliphist-mcp.sh: registering the cliphist MCP server for normal
Claude Code sessions (user scope) through voice/cliphist-mcp.sh. `claude` is a stub on PATH
that logs its argv; `mcp add` records the registered command, `mcp get` prints it like the real
CLI (`  Scope: ...`, `  Command: ...`) once there is one.
No phone, no network, never the real `claude mcp add`.
Run:  .venv/bin/python -m pytest -q
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "register-cliphist-mcp.sh"
WRAPPER = REPO / "voice" / "cliphist-mcp.sh"
TOKEN = "t0ken-not-real"

STUB_CLAUDE = """#!/bin/sh
printf '%s\\n' "$*" >>"$STUB_LOG/claude"
case "$1 $2" in
  "mcp get") [ -e "$STUB_LOG/registered" ] || { echo "No MCP server found with name: $3" >&2; exit 1; }
             echo "$3:"; echo "  Scope: User config"; echo "  Type: stdio"
             cat "$STUB_LOG/registered" ;;
  "mcp add") for a; do last=$a; done
             printf '  Command: %s\n' "$last" >"$STUB_LOG/registered"
             echo "Added stdio MCP server $5 with command: $last" ;;
esac
"""
WARN = "registered server will use the wrapper defaults"


@pytest.fixture()
def reg(tmp_path):
    """Runs the script with the stub `claude` first on PATH; token and server live in tmp_path."""
    bindir, log = tmp_path / "bin", tmp_path / "stub"
    bindir.mkdir()
    log.mkdir()
    (bindir / "claude").write_text(STUB_CLAUDE)
    (bindir / "claude").chmod(0o755)
    token, server = tmp_path / "cliphist-token", tmp_path / "index.js"
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "HOME": str(tmp_path),
           "STUB_LOG": str(log), "CLIPHIST_TOKEN_FILE": str(token), "CLIPHIST_MCP": str(server)}

    def run(*args, unset=()):
        e = {k: v for k, v in env.items() if k not in unset}
        p = subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=e, timeout=30)
        logf = log / "claude"
        return p, logf.read_text().splitlines() if logf.exists() else []
    run.token, run.server, run.registered = token, server, log / "registered"
    return run


def _ready(reg):
    reg.token.write_text(TOKEN + "\n")
    reg.server.write_text("")


def _adds(calls):
    return [c for c in calls if c.startswith("mcp add")]


def test_script_is_executable_bash():
    assert os.access(SCRIPT, os.X_OK)
    assert SCRIPT.read_text().startswith("#!/usr/bin/env bash\n")
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_dry_run_prints_the_command_and_changes_nothing(reg):
    _ready(reg)
    p, calls = reg()
    assert p.returncode == 0, p.stderr
    assert f"claude mcp add --scope user cliphist -- {WRAPPER}" in p.stdout
    assert calls == []  # dry-run never calls claude, so no add
    assert TOKEN not in p.stdout + p.stderr


def test_dry_run_without_setup_still_exits_0(reg):
    p, calls = reg()
    assert p.returncode == 0, p.stderr
    assert "refuses" in p.stdout and calls == []


def test_unknown_argument_is_refused(reg):
    p, calls = reg("--aply")
    assert p.returncode == 2 and "usage" in p.stderr and calls == []


def test_apply_refuses_without_token(reg):
    reg.server.write_text("")
    p, calls = reg("--apply")
    assert p.returncode != 0 and "no token" in p.stderr
    assert _adds(calls) == []
    reg.token.write_text("")  # empty counts as missing
    p, calls = reg("--apply")
    assert p.returncode != 0 and _adds(calls) == []


def test_apply_refuses_without_built_server(reg):
    reg.token.write_text(TOKEN + "\n")
    p, calls = reg("--apply")
    assert p.returncode != 0 and "not built" in p.stderr
    assert _adds(calls) == []
    assert TOKEN not in p.stdout + p.stderr


def test_apply_adds_user_scope_through_the_wrapper_then_is_a_noop(reg):
    _ready(reg)
    p, calls = reg("--apply")
    assert p.returncode == 0, p.stderr
    assert _adds(calls) == [f"mcp add --scope user cliphist -- {WRAPPER}"]
    assert calls[0] == "mcp get cliphist"
    out1 = p.stdout + p.stderr

    p, calls = reg("--apply")
    assert p.returncode == 0, p.stderr
    assert "already registered" in p.stdout
    assert len(_adds(calls)) == 1  # still only the first add
    out2 = p.stdout + p.stderr
    assert TOKEN not in "\n".join(calls) + out1 + out2


def test_apply_is_a_noop_when_registered_even_without_token(reg):
    reg.registered.write_text(f"  Command: {WRAPPER}\n")
    p, calls = reg("--apply")
    assert p.returncode == 0 and "already registered" in p.stdout
    assert calls == ["mcp get cliphist"]


def test_apply_stops_at_a_foreign_cliphist_entry_and_never_prints_it(reg):
    _ready(reg)
    reg.registered.write_text("  Command: node\n  Args: /root/cliphist-android/mcp-server/dist/index.js\n"
                              f"  Environment:\n    CLIPHIST_API_TOKEN={TOKEN}\n")
    p, calls = reg("--apply")
    assert p.returncode == 2, p.stdout + p.stderr
    assert calls == ["mcp get cliphist"] and _adds(calls) == []
    assert "already registered" not in p.stdout
    assert "claude mcp get cliphist" in p.stderr and "claude mcp remove cliphist -s" in p.stderr
    assert TOKEN not in p.stdout + p.stderr and "Environment" not in p.stdout + p.stderr


@pytest.mark.parametrize("var", ["CLIPHIST_TOKEN_FILE", "CLIPHIST_MCP"])
def test_override_env_vars_warn_that_registration_uses_wrapper_defaults(reg, var):
    _ready(reg)
    other = "CLIPHIST_MCP" if var == "CLIPHIST_TOKEN_FILE" else "CLIPHIST_TOKEN_FILE"
    p, _ = reg(unset=(other,))  # dry-run, only `var` set
    assert p.returncode == 0 and WARN in p.stderr
    assert len([l for l in p.stderr.splitlines() if WARN in l]) == 1
    p, calls = reg("--apply")  # both set
    assert p.returncode == 0 and WARN in p.stderr
    assert _adds(calls) == [f"mcp add --scope user cliphist -- {WRAPPER}"]
    assert TOKEN not in p.stdout + p.stderr


def test_no_warning_without_override_env_vars(reg):
    p, calls = reg(unset=("CLIPHIST_TOKEN_FILE", "CLIPHIST_MCP"))  # dry-run only: reads the defaults
    assert p.returncode == 0 and WARN not in p.stdout + p.stderr and calls == []
