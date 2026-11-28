"""Tests for remote.py, the tailnet HTTP server (mcp-prompts#141). No phone and no network: the
ASGI app runs in-process and the adb call behind notify is stubbed.   Run:  .venv/bin/python -m pytest -q
"""
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))

import remote  # noqa: E402

TOKEN = "t" * 40
NODE = "phone.tail0000.ts.net"
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(client, method, params=None, *, token=TOKEN, host=NODE, extra=None):
    headers = {**ACCEPT, "Host": f"{host}:{remote.DEFAULT_PORT}"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(extra or {})
    body = {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params else {})}
    return client.post("/mcp", json=body, headers=headers)


@pytest.fixture()
def client():
    with TestClient(remote.build_app(TOKEN, [NODE, "100.64.0.7"]), base_url=f"http://{NODE}") as c:
        yield c


def test_serves_only_the_remote_tools(client):
    reply = rpc(client, "tools/list")
    assert reply.status_code == 200
    names = sorted(tool["name"] for tool in reply.json()["result"]["tools"])
    assert names == sorted(remote.REMOTE_TOOLS)
    for absent in ("act", "open", "run_tasker_task", "mqtt_publish", "ask_ai", "phone_command", "listen"):
        assert absent not in names


def test_keeps_the_descriptions_and_annotations_of_the_local_tools(client):
    tools = {tool["name"]: tool for tool in rpc(client, "tools/list").json()["result"]["tools"]}
    assert tools["screen"]["annotations"]["readOnlyHint"] is True
    assert "numbered elements" in tools["screen"]["description"]


def test_refuses_requests_without_the_right_token(client):
    for token in (None, "wrong", TOKEN + "x", ""):
        reply = rpc(client, "tools/list", token=token)
        assert reply.status_code == 401, token
        assert reply.headers["www-authenticate"].startswith("Bearer")
    # The scheme must be Bearer.
    reply = client.post("/mcp", json={}, headers={**ACCEPT, "Host": NODE, "Authorization": f"Basic {TOKEN}"})
    assert reply.status_code == 401


def test_refuses_other_host_names_and_browser_origins(client):
    assert rpc(client, "tools/list", host="evil.example").status_code in (403, 421)
    assert rpc(client, "tools/list", host="100.64.0.7").status_code == 200
    assert rpc(client, "tools/list", extra={"Origin": "https://evil.example"}).status_code == 403


def test_cannot_call_a_local_only_tool(client):
    reply = rpc(client, "tools/call", {"name": "act", "arguments": {"action": "home"}})
    result = reply.json()
    assert "error" in result or result["result"].get("isError") is True


def test_notify_posts_through_adb(client, monkeypatch):
    calls = []
    monkeypatch.setattr(remote.local, "_adb", lambda cmd, timeout=20: (calls.append(cmd), (0, ""))[1])
    reply = rpc(client, "tools/call", {"name": "notify", "arguments": {"title": "From desktop", "text": "Build done"}})
    assert reply.status_code == 200
    assert "posted" in reply.text
    assert calls and calls[0].startswith("cmd notification post") and "'Build done'" in calls[0]


def test_token_file_must_be_private_and_long(tmp_path):
    path = tmp_path / "token"
    path.write_text(TOKEN + "\n")
    path.chmod(0o600)
    assert remote.read_token(path) == TOKEN
    path.chmod(0o644)
    with pytest.raises(remote.RemoteConfigError, match="chmod 600"):
        remote.read_token(path)
    path.write_text("short")
    path.chmod(0o600)
    with pytest.raises(remote.RemoteConfigError, match="shorter"):
        remote.read_token(path)
    with pytest.raises(remote.RemoteConfigError, match="cannot read"):
        remote.read_token(tmp_path / "missing")


def test_refuses_every_interface_and_a_missing_host_list():
    with pytest.raises(remote.RemoteConfigError, match="tailnet address"):
        remote.check_settings("0.0.0.0", [NODE])
    with pytest.raises(remote.RemoteConfigError, match="--allowed-hosts"):
        remote.check_settings("100.64.0.7", [])
    remote.check_settings("100.64.0.7", [NODE])
    remote.check_settings("127.0.0.1", [])


def test_main_exits_2_on_bad_settings(tmp_path, capsys):
    assert remote.main(["--host", "100.64.0.7", "--allowed-hosts", NODE]) == 2
    assert "--token-file is required" in capsys.readouterr().err
