"""Offline tests for voice/eleven.py's text-to-speech fallbacks: ElevenLabs, then Microsoft Edge
neural voices (edge-tts), then espeak-ng."""
import importlib.util
import io
import shutil
import sys
import types
from pathlib import Path

import pytest

ELEVEN = Path(__file__).resolve().parent.parent / "voice" / "eleven.py"


class QuotaError(Exception):
    status_code = 401
    body = {"detail": {"code": "quota_exceeded"}}


@pytest.fixture
def eleven(monkeypatch):
    monkeypatch.setitem(sys.modules, "dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "elevenlabs", types.SimpleNamespace(ElevenLabs=object))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-not-a-key")
    spec = importlib.util.spec_from_file_location("eleven", ELEVEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def client(fail=None, chunks=(b"ID3", b"mp3")):
    def convert(**k):
        if fail:
            raise fail
        return iter(chunks)
    return types.SimpleNamespace(text_to_speech=types.SimpleNamespace(convert=convert))


def run_tts(eleven, monkeypatch, cl, text, out):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    eleven.tts(cl, str(out))


def broken(msg):
    def engine(*a):
        raise RuntimeError(msg)
    return engine


def test_elevenlabs_success_writes_its_audio(eleven, monkeypatch, tmp_path):
    out = tmp_path / "a.mp3"
    monkeypatch.setattr(eleven, "edge_tts", lambda *a: pytest.fail("no fallback on success"))
    monkeypatch.setattr(eleven, "offline_tts", lambda *a: pytest.fail("no fallback on success"))
    run_tts(eleven, monkeypatch, client(), "hello", out)
    assert out.read_bytes() == b"ID3mp3"


def test_quota_error_falls_back_to_edge_voice(eleven, monkeypatch, tmp_path, capsys):
    out, calls = tmp_path / "a.mp3", []
    monkeypatch.setattr(eleven, "edge_tts", lambda text, o: calls.append((text, o)) or Path(o).write_bytes(b"x"))
    monkeypatch.setattr(eleven, "offline_tts", lambda *a: pytest.fail("edge worked; no espeak"))
    run_tts(eleven, monkeypatch, client(QuotaError()), "Dobrý den", out)
    assert calls == [("Dobrý den", str(out))] and out.exists()
    err = capsys.readouterr().err
    assert "quota_exceeded" in err and "edge-tts (cs-CZ-AntoninNeural)" in err and "test-not-a-key" not in err


def test_edge_failing_falls_back_to_espeak(eleven, monkeypatch, tmp_path, capsys):
    out, calls = tmp_path / "a.mp3", []
    monkeypatch.setattr(eleven, "edge_tts", broken("no network"))
    monkeypatch.setattr(eleven, "offline_tts", lambda text, o: calls.append(text) or Path(o).write_bytes(b"x"))
    run_tts(eleven, monkeypatch, client(QuotaError()), "Hello", out)
    assert calls == ["Hello"]
    err = capsys.readouterr().err
    assert "espeak-ng (en)" in err and "edge-tts: RuntimeError: no network" in err


def test_edge_can_be_skipped(eleven, monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_EDGE", "0")
    with pytest.raises(RuntimeError, match="TTS_EDGE=0"):
        eleven.edge_tts("hello", str(tmp_path / "a.mp3"))


def test_fallback_can_be_turned_off(eleven, monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_OFFLINE_FALLBACK", "0")
    monkeypatch.setattr(eleven, "edge_tts", lambda *a: pytest.fail("fallback disabled"))
    monkeypatch.setattr(eleven, "offline_tts", lambda *a: pytest.fail("fallback disabled"))
    with pytest.raises(QuotaError):
        run_tts(eleven, monkeypatch, client(QuotaError()), "hello", tmp_path / "a.mp3")


def test_all_failing_exits_with_one_line(eleven, monkeypatch, tmp_path):
    monkeypatch.setattr(eleven, "edge_tts", broken("no network"))
    monkeypatch.setattr(eleven, "offline_tts", broken("espeak-ng or sox is not installed"))
    with pytest.raises(SystemExit) as e:
        run_tts(eleven, monkeypatch, client(QuotaError()), "hello", tmp_path / "a.mp3")
    msg = str(e.value)
    assert msg.startswith("tts failed: ElevenLabs:") and "quota_exceeded" in msg
    assert "edge-tts: RuntimeError: no network" in msg and "espeak-ng: RuntimeError" in msg and "\n" not in msg


@pytest.mark.parametrize(
    "text, voice", [("Příliš žluťoučký kůň", "cs"), ("Dobrý den", "cs"), ("Hello there", "en"), ("Ahoj", "en"), ("Qué tal", "en")]
)
def test_voice_follows_czech_letters(eleven, monkeypatch, text, voice):
    monkeypatch.delenv("TTS_FALLBACK_VOICE", raising=False)
    assert eleven.offline_voice(text) == voice
    monkeypatch.setenv("TTS_FALLBACK_VOICE", "de")
    assert eleven.offline_voice(text) == "de"
    assert eleven.edge_voice(text) == ("cs-CZ-AntoninNeural" if voice == "cs" else "en-US-AndrewMultilingualNeural")


@pytest.mark.skipif(not (shutil.which("espeak-ng") and shutil.which("sox")), reason="espeak-ng and sox not installed")
def test_offline_tts_makes_a_real_mp3(eleven, tmp_path):
    out = tmp_path / "say.mp3"
    eleven.offline_tts("Dobrý den", str(out))
    head = out.read_bytes()[:3]
    assert head == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    assert not (tmp_path / "say.mp3.part").exists()
