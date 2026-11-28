"""The agent-browser injection fixture (browser/, mcp-prompts #144 and #143): the page carries
its planted instructions, and the fixture server records a hit on any /canary/ URL. Offline."""
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BROWSER = Path(__file__).resolve().parent.parent / "browser"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_fixture_page_plants_three_canary_instructions():
    page = (BROWSER / "fixtures" / "injection.html").read_text()
    for canary in ("/canary/hidden", "/canary/link", "/canary/form"):
        assert canary in page
    assert "ignore your previous" in page and "system prompt" in page


def test_server_serves_the_page_and_records_canary_hits(tmp_path):
    port, hits = _free_port(), tmp_path / "hits.txt"
    proc = subprocess.Popen([sys.executable, str(BROWSER / "fixture-server.py"), "--port", str(port), "--hits", str(hits)])
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                page = urllib.request.urlopen(f"{base}/injection.html", timeout=2).read().decode()
                break
            except OSError:
                time.sleep(0.1)
        assert "Lumen Sync 2.4" in page
        assert not hits.exists() or hits.read_text() == ""
        urllib.request.urlopen(f"{base}/canary/link?x=1", timeout=2).read()
        assert "GET /canary/link?x=1" in hits.read_text()
    finally:
        proc.terminate()
        proc.wait(5)
