"""Tests for assistant/phone/phone-services (bash) with stub commands on PATH. No phone needed.

The stubs replace `timeout` (which every probe goes through, including the /dev/tcp port check),
`adb` (with per-call scripted results), `pactl`, `curl`, `ssh`, `sleep` and `pkill`. Each stub appends its argv to calls.log.
Run:  .venv/bin/python -m pytest -q
"""
import os
import pty
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "phone" / "phone-services"

STUBS = {
    # timeout DUR CMD...: emulate port probes from STUB_PORTS, run everything else
    "timeout": r'''echo "timeout $*" >>"$STUB_DIR/calls.log"; shift
if [ "$1" = bash ] && [ "$2" = -c ]; then
  port=${3##*/}; case " $STUB_PORTS " in *" $port "*) exit 0;; *) exit 1;; esac
fi
exec "$@"''',
    # adb: `shell true` succeeds once $STUB_DIR/adb-up exists; `connect` creates it if STUB_CONNECT_WORKS=1
    "adb": r'''echo "adb $*" >>"$STUB_DIR/calls.log"
# scripted results: each line of $STUB_DIR/adb-rules is "<rc> <glob>"; the first glob that
# matches the full argument string decides the exit code
if [ -f "$STUB_DIR/adb-rules" ]; then
  while read -r rc glob; do
    case "$*" in $glob) exit "$rc";; esac
  done <"$STUB_DIR/adb-rules"
fi
case "$*" in
  connect*) [ "${STUB_CONNECT_WORKS:-0}" = 1 ] && touch "$STUB_DIR/adb-up"; echo "connected"; exit 0;;
  *"shell nohup sh "*) touch "$STUB_DIR/pa-up"; exit 0;;  # plain launch: pactl sees the source from now on
  *"shell true") [ -f "$STUB_DIR/adb-up" ];;
  *) exit 0;;
esac''',
    "pactl": r'''echo "pactl $*" >>"$STUB_DIR/calls.log"; { [ "${STUB_PA_UP:-0}" = 1 ] || [ -f "$STUB_DIR/pa-up" ]; } && echo "0	OpenSL_ES_source	module-sles-source.c"; exit 0''',
    "curl": r'''echo "curl $*" >>"$STUB_DIR/calls.log"; [ "${STUB_VA_UP:-0}" = 1 ]''',
    "ssh": r'''echo "ssh $*" >>"$STUB_DIR/calls.log"; exit 0''',
    "sleep": r'''exit 0''',
    "pkill": r'''echo "pkill $*" >>"$STUB_DIR/calls.log"; exit 1''',
}


@pytest.fixture()
def ps(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    for name, body in STUBS.items():
        f = stub / name
        f.write_text("#!/bin/bash\n" + body + "\n")
        f.chmod(0o755)
    key = tmp_path / "key"
    key.write_text("not a real key\n")
    base_env = {
        "PATH": f"{stub}:/usr/bin:/bin",
        "STUB_DIR": str(tmp_path),
        "PHONE_DIR": str(tmp_path / "phone"),
        "VA_DIR": str(tmp_path / "va"),
        "PHONE_SSH_KEY": str(key),
        "HOME": str(tmp_path),
    }

    def run(*args, adb_up=False, tty=False, **env):
        # stdin decides the su gate: /dev/null by default, a pty slave for tty=True (a human at a terminal)
        if adb_up:
            (tmp_path / "adb-up").touch()
        master, slave = pty.openpty() if tty else (None, None)
        try:
            return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=30,
                                  stdin=slave if tty else subprocess.DEVNULL, env={**base_env, **env})
        finally:
            if tty:
                os.close(master)
                os.close(slave)

    def calls():
        log = tmp_path / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def rules(*lines):
        (tmp_path / "adb-rules").write_text("\n".join(lines) + "\n")

    run.calls = calls
    run.rules = rules
    run.dir = tmp_path
    return run


def test_syntax():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_status_all_up(ps):
    p = ps("status", adb_up=True, STUB_PORTS="4713 8022", STUB_PA_UP="1", STUB_VA_UP="1")
    out = p.stdout.splitlines()
    assert out[0].startswith("adb      : OK (127.0.0.1:5555)")
    assert out[1].startswith("mic      : OK")
    assert out[2].startswith("va-http  : OK")
    assert out[3] == "native   : sshd on 8022, key login OK"
    assert out[4] == "agents   : not installed (assistant/agents/install.sh)"


def test_status_all_down(ps):
    out = ps("status", STUB_PORTS="").stdout.splitlines()
    assert out == ["adb      : DOWN (port 5555 closed)", "mic      : DOWN",
                   "va-http  : DOWN", "native   : sshd not running",
                   "agents   : not installed (assistant/agents/install.sh)"]


def test_status_reports_open_port_when_adb_down(ps):
    assert ps("status", STUB_PORTS="5555").stdout.splitlines()[0] == "adb      : DOWN (port 5555 open)"


def test_status_does_not_adb_connect(ps):
    ps("status", STUB_PORTS="5555")
    assert not [c for c in ps.calls() if c.startswith("adb connect")]


def test_ensure_skips_adb_connect_when_link_is_up(ps):
    ps("ensure", adb_up=True, STUB_PORTS="4713", STUB_PA_UP="1", STUB_VA_UP="1")
    assert not [c for c in ps.calls() if c.startswith("adb connect")]
    log = (ps.dir / "phone" / "logs" / "ensure.log").read_text()
    assert "adb ok" in log and "mic ok" in log and "va-http ok" in log


def test_ensure_reconnects_when_link_is_down_and_port_open(ps):
    ps("ensure", STUB_PORTS="5555 4713", STUB_PA_UP="1", STUB_VA_UP="1", STUB_CONNECT_WORKS="1")
    assert [c for c in ps.calls() if c.startswith("adb connect")] == ["adb connect 127.0.0.1:5555"]
    assert "adb ok" in (ps.dir / "phone" / "logs" / "ensure.log").read_text()


def test_ensure_does_not_connect_when_port_closed(ps):
    ps("ensure", STUB_PORTS="4713", STUB_PA_UP="1", STUB_VA_UP="1", STUB_CONNECT_WORKS="1")
    assert not [c for c in ps.calls() if c.startswith("adb connect")]
    assert "adb down" in (ps.dir / "phone" / "logs" / "ensure.log").read_text()


def test_ensure_is_single_instance(ps):
    logs = ps.dir / "phone" / "logs"
    logs.mkdir(parents=True)
    locker = _held_lock(logs / ".lock")
    try:
        p = ps("ensure", adb_up=True, STUB_PORTS="4713", STUB_PA_UP="1", STUB_VA_UP="1")
    finally:
        locker.communicate(b"\n", timeout=5)
    assert p.returncode == 0 and not (logs / "ensure.log").exists() and not ps.calls()


def _held_lock(path):
    """Hold an exclusive flock on path in another process; returns it (stdin closes it)."""
    locker = subprocess.Popen(["flock", "-x", str(path), "-c", "read _"], stdin=subprocess.PIPE)
    for _ in range(100):
        if subprocess.run(["flock", "-n", str(path), "true"]).returncode:
            return locker
        time.sleep(0.05)
    raise AssertionError("lock not taken")


def _va_pid(ps, *argv):
    va_state = ps.dir / "va" / "state"
    va_state.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", *argv])
    (va_state / "http.pid").write_text(f"{proc.pid}\n")
    return proc, va_state / "http.pid"


def test_va_stop_kills_trigger_by_pid_file_not_pkill(ps):
    proc, pidfile = _va_pid(ps, "http_trigger.py")
    ps("va-restart", STUB_PORTS="")  # no http_trigger.py in VA_DIR: stop succeeds, start fails
    assert proc.wait(timeout=5) != 0  # killed by signal
    assert not pidfile.exists()
    assert not [c for c in ps.calls() if c.startswith("pkill")]


def test_va_stop_ignores_stale_pid_of_unrelated_process(ps):
    proc, pidfile = _va_pid(ps, "something-else")
    try:
        ps("va-restart", STUB_PORTS="")
        assert proc.poll() is None  # still running
        assert not pidfile.exists()
        assert not [c for c in ps.calls() if c.startswith("pkill")]
    finally:
        proc.kill()


def test_watch_second_instance_exits_immediately(ps):
    logs = ps.dir / "phone" / "logs"
    logs.mkdir(parents=True)
    locker = _held_lock(logs / ".watch.lock")
    try:
        p = ps("watch", "1", adb_up=True, STUB_PORTS="4713", STUB_PA_UP="1", STUB_VA_UP="1")
    finally:
        locker.communicate(b"\n", timeout=5)
    assert p.returncode == 0 and not ps.calls() and not (logs / "ensure.log").exists()


def test_pa_stop_falls_back_to_su_when_plain_pkill_does_not_stop_it(ps):
    ps.rules("0 *shell pkill*", "0 *shell pgrep*", "0 *su -c*")  # pgrep keeps finding it
    p = ps("pa-stop", tty=True)
    shell = [c for c in ps.calls() if c.startswith("adb -s 127.0.0.1:5555 shell")]
    assert "pkill -f '[/]data/local/tmp/pa/bin/pulseaudio'" in shell[0] and "su -c" not in shell[0]
    assert any("su -c" in c and "pkill -f '[/]data/local/tmp/pa/bin/pulseaudio'" in c for c in shell)
    assert p.stdout.strip() == "still running"


def test_pa_stop_without_tty_never_uses_su(ps):
    ps.rules("0 *shell pkill*", "0 *shell pgrep*", "0 *su -c*")  # su would work, but nobody is at a terminal
    p = ps("pa-stop")
    assert not [c for c in ps.calls() if "su -c" in c]
    assert p.stdout.strip() == "still running" and "phone-services pa-stop in a terminal" in p.stderr


def test_pa_stop_without_su_when_plain_pkill_works(ps):
    ps.rules("0 *shell pkill*", "1 *shell pgrep*")  # gone after the plain pkill
    assert ps("pa-stop").stdout.strip() == "stopped"
    assert not [c for c in ps.calls() if "su -c" in c]


def _pa_start(ps, su_rc, tty=True):
    # bundle present, mic already answers (so Termux's own pulseaudio is never touched)
    ps.rules("0 *test -x /data/local/tmp/pa/bin/pulseaudio*", f"{su_rc} *su -c*su 2000*", "0 *nohup sh*")
    p = ps("pa-restart", adb_up=True, tty=tty, STUB_PORTS="4713", STUB_PA_UP="1")
    return p, [c for c in ps.calls() if "run.sh" in c]


def test_pa_start_launches_through_magisk_su_as_uid_2000(ps):
    p, launches = _pa_start(ps, 0)
    assert p.stdout.strip() == "mic server up"
    assert launches == ["adb -s 127.0.0.1:5555 shell su -c \"su 2000 -c "
                        "'nohup sh /data/local/tmp/pa/run.sh >/dev/null 2>&1 &'\""]


def test_pa_start_falls_back_to_plain_adb_child_when_su_fails(ps):
    p, launches = _pa_start(ps, 1)
    assert p.stdout.strip() == "mic server up"
    assert len(launches) == 2 and "su 2000" in launches[0]
    assert launches[1] == "adb -s 127.0.0.1:5555 shell nohup sh /data/local/tmp/pa/run.sh >/dev/null 2>&1 &"


def test_pa_start_without_tty_launches_plain_adb_child_only(ps):
    p, launches = _pa_start(ps, 0, tty=False)  # su would work, but nobody is at a terminal
    assert p.stdout.strip() == "mic server up" and "no tty" in p.stderr
    assert launches == ["adb -s 127.0.0.1:5555 shell nohup sh /data/local/tmp/pa/run.sh >/dev/null 2>&1 &"]
    assert not [c for c in ps.calls() if "su -c" in c or "su 2000" in c]


def _no_su(ps):
    return not [c for c in ps.calls() if "su -c" in c or "su 2000" in c]


@pytest.mark.parametrize("tty", [False, True])
def test_ensure_with_mic_down_never_uses_su(ps, tty):
    # su would be granted and the mic never comes up: ensure must still not try root
    ps.rules("0 *su -c*", "0 *test -x /data/local/tmp/pa/bin/pulseaudio*", "0 *nohup sh*")
    ps("ensure", adb_up=True, tty=tty, STUB_PORTS="", STUB_VA_UP="1")
    assert _no_su(ps)
    assert [c for c in ps.calls() if c.endswith("shell nohup sh /data/local/tmp/pa/run.sh >/dev/null 2>&1 &")]
    assert "mic DOWN - run: phone-services pa-restart" in (ps.dir / "phone" / "logs" / "ensure.log").read_text()


def test_ensure_with_mic_down_starts_plain_child_and_names_pa_restart(ps):
    ps.rules("0 *su -c*", "0 *test -x /data/local/tmp/pa/bin/pulseaudio*")  # the plain launch brings it up
    ps("ensure", adb_up=True, STUB_PORTS="4713", STUB_VA_UP="1")
    assert _no_su(ps)
    log = (ps.dir / "phone" / "logs" / "ensure.log").read_text()
    assert "mic started as adb child, no su" in log and "run: phone-services pa-restart" in log


def test_ensure_with_bundle_missing_pushes_without_su(ps):
    # bundle gone and an old server still found by pgrep: pa_push -> pa_kill must not escalate
    bundle = ps.dir / "phone" / "pa"
    bundle.mkdir(parents=True)
    (bundle / "build-bundle.sh").write_text("#!/bin/bash\nexit 0\n")
    (bundle / "build-bundle.sh").chmod(0o755)
    ps.rules("0 *su -c*", "1 *test -x /data/local/tmp/pa/bin/pulseaudio*", "0 *shell pkill*", "0 *shell pgrep*")
    ps("ensure", adb_up=True, STUB_PORTS="", STUB_VA_UP="1")
    assert [c for c in ps.calls() if "shell pkill" in c]  # reached pa_kill
    assert _no_su(ps)
    log = (ps.dir / "phone" / "logs" / "ensure.log").read_text()
    assert "could not stop the old mic server" in log and "mic DOWN - run: phone-services pa-restart" in log


def test_unknown_command_prints_usage(ps):
    p = ps("nonsense")
    assert p.returncode == 2 and "phone-services ensure" in p.stdout


def test_native_without_key_fails_cleanly(ps):
    p = ps("native", "true", PHONE_SSH_KEY=str(ps.dir / "missing"))
    assert p.returncode == 1 and "no key" in p.stderr
    assert not [c for c in ps.calls() if c.startswith("ssh")]
