"""Tests for scripts/register-cliphist-mcp.sh: registering the cliphist MCP server for normal
Claude Code sessions (user scope) through voice/cliphist-mcp.sh. `claude` is a stub on PATH
that logs its argv; `mcp add` marks the server registered, `mcp get` succeeds once it is.
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
             echo "$3: registered" ;;
  "mcp add") touch "$STUB_LOG/registered"; echo "Added stdio MCP server $3" ;;
esac
"""


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

    def run(*args):
        p = subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30)
        logf = log / "claude"
        return p, logf.read_text().splitlines() if logf.exists() else []
    run.token, run.server = token, server
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


def test_apply_is_a_noop_when_registered_even_without_token(reg, tmp_path):
    (tmp_path / "stub" / "registered").touch()
    p, calls = reg("--apply")
    assert p.returncode == 0 and "already registered" in p.stdout
    assert calls == ["mcp get cliphist"]
