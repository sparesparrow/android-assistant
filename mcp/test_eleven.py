"""Offline tests for voice/eleven.py's fallbacks. Text-to-speech: ElevenLabs, then the phone's
Android TTS (android-say.sh) and Microsoft Edge neural voices (edge-tts), in an order that follows
the text's language, then espeak-ng. Speech-to-text: ElevenLabs, then faster-whisper."""
import importlib.util
import io
import json
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
def eleven(monkeypatch, tmp_path):
    monkeypatch.setenv("VA_STATE_DIR", str(tmp_path / "state"))  # cooldown state per test
    monkeypatch.delenv("ENGINE_COOLDOWN", raising=False)
    monkeypatch.setitem(sys.modules, "dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "elevenlabs", types.SimpleNamespace(ElevenLabs=object))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-not-a-key")
    spec = importlib.util.spec_from_file_location("eleven", ELEVEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("ANDROID_TTS_LANG", "cs")  # never ask a real phone
    monkeypatch.setattr(mod, "android_tts", broken("no phone in tests"))
    return mod


def client(fail=None, chunks=(b"ID3", b"mp3")):
    def convert(**k):
        if fail:
            raise fail
        return iter(chunks)
    return types.SimpleNamespace(text_to_speech=types.SimpleNamespace(convert=convert))


def broken(msg):
    def engine(*a):
        raise RuntimeError(msg)
    return engine


def run_tts(eleven, monkeypatch, cl, text, out):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    eleven.tts(cl, str(out))


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
    "text, voice",
    [("Příliš žluťoučký kůň", "cs"), ("Dobrý den", "cs"), ("Hello there", "en"), ("Ahoj", "en"), ("Qué tal", "en")],
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


def test_czech_text_goes_to_the_phone_first(eleven, monkeypatch, tmp_path, capsys):
    out, calls = tmp_path / "a.mp3", []
    monkeypatch.setattr(eleven, "android_tts", lambda text, o: calls.append(text) or Path(o).write_bytes(b"x"))
    monkeypatch.setattr(eleven, "edge_tts", lambda *a: pytest.fail("the phone spoke; no edge"))
    run_tts(eleven, monkeypatch, client(QuotaError()), "Dnes je čtvrtek.", out)
    assert calls == ["Dnes je čtvrtek."]
    assert "spoke with android-say" in capsys.readouterr().err


def test_text_in_another_language_goes_to_edge_first(eleven, monkeypatch, tmp_path):
    out, calls = tmp_path / "a.mp3", []
    monkeypatch.setattr(eleven, "android_tts", lambda *a: pytest.fail("phone speaks Czech; edge first"))
    monkeypatch.setattr(eleven, "edge_tts", lambda text, o: calls.append(text) or Path(o).write_bytes(b"x"))
    run_tts(eleven, monkeypatch, client(QuotaError()), "Today is Thursday.", out)
    assert calls == ["Today is Thursday."]


def test_phone_is_second_when_edge_fails_for_other_language(eleven, monkeypatch):
    monkeypatch.setenv("ANDROID_TTS_LANG", "cs")
    assert [n for n, *_ in eleven.fallback_chain("Hello")] == ["edge-tts", "android-say", "espeak-ng"]
    assert [n for n, *_ in eleven.fallback_chain("Dobrý den")] == ["android-say", "edge-tts", "espeak-ng"]
    monkeypatch.setenv("ANDROID_TTS_LANG", "en")
    assert [n for n, *_ in eleven.fallback_chain("Hello")][0] == "android-say"


@pytest.mark.parametrize(
    "stdout, lang", [("com.google.android.tts:cs_CZ\n", "cs"), ("en-US", "en"), ("null", None), ("", None)]
)
def test_android_tts_lang_reads_the_phone_setting(eleven, monkeypatch, stdout, lang):
    monkeypatch.delenv("ANDROID_TTS_LANG")
    monkeypatch.setattr(eleven.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=stdout, returncode=0))
    assert eleven.android_tts_lang() == lang


def load_real_android_tts(monkeypatch, eleven, script, tmp_path):
    """Re-import to get the real android_tts, pointed at a fake android-say.sh in tmp_path."""
    spec = importlib.util.spec_from_file_location("eleven_real", ELEVEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fake = tmp_path / "android-say.sh"
    fake.write_text("#!/bin/sh\n" + script)
    fake.chmod(0o755)
    monkeypatch.setattr(mod, "HERE", str(tmp_path))
    return mod


def test_android_tts_can_be_skipped(eleven, monkeypatch, tmp_path):
    mod = load_real_android_tts(monkeypatch, eleven, "exit 0\n", tmp_path)
    monkeypatch.setenv("TTS_ANDROID", "0")
    with pytest.raises(RuntimeError, match="TTS_ANDROID=0"):
        mod.android_tts("hello", str(tmp_path / "a.mp3"))


@pytest.mark.skipif(not (shutil.which("adb") and shutil.which("sox")), reason="adb and sox not installed")
def test_android_tts_waits_and_leaves_silence(eleven, monkeypatch, tmp_path):
    mod = load_real_android_tts(monkeypatch, eleven, 'cat > "$(dirname "$0")/said.txt"; [ "$1" = --wait ]\n', tmp_path)
    out = tmp_path / "a.mp3"
    mod.android_tts("Dobrý den", str(out))
    assert (tmp_path / "said.txt").read_text() == "Dobrý den"
    assert out.stat().st_size > 0 and not (tmp_path / "a.mp3.part").exists()


@pytest.mark.skipif(not shutil.which("adb"), reason="adb not installed")
def test_android_tts_failure_carries_the_last_stderr_line(eleven, monkeypatch, tmp_path):
    script = "cat >/dev/null; echo 'android-say: Tasker did not start speaking' >&2; exit 3\n"
    mod = load_real_android_tts(monkeypatch, eleven, script, tmp_path)
    with pytest.raises(RuntimeError, match="did not start speaking"):
        mod.android_tts("hello", str(tmp_path / "a.mp3"))


# --- speech-to-text ---

def stt_client(fail=None):
    def convert(**k):
        if fail:
            raise fail
        return types.SimpleNamespace(text=" Ahoj ", language_code="ces")
    return types.SimpleNamespace(speech_to_text=types.SimpleNamespace(convert=convert))


def run_stt(eleven, cl, tmp_path, capsys):
    audio = tmp_path / "in.m4a"
    audio.write_bytes(b"audio")
    eleven.stt(cl, str(audio))
    return capsys.readouterr()


def test_stt_elevenlabs_success(eleven, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(eleven, "whisper_stt", lambda p: pytest.fail("no fallback on success"))
    r = run_stt(eleven, stt_client(), tmp_path, capsys)
    assert json.loads(r.out) == {"text": "Ahoj", "language_code": "ces"}


def test_stt_quota_falls_back_to_whisper(eleven, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(eleven, "whisper_stt", lambda p: ("Rozsviť světla", "ces"))
    r = run_stt(eleven, stt_client(QuotaError()), tmp_path, capsys)
    assert json.loads(r.out) == {"text": "Rozsviť světla", "language_code": "ces"}
    assert "faster-whisper (base)" in r.err and "quota_exceeded" in r.err and "test-not-a-key" not in r.err


def test_stt_fallback_can_be_turned_off(eleven, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("STT_OFFLINE_FALLBACK", "0")
    monkeypatch.setattr(eleven, "whisper_stt", lambda p: pytest.fail("fallback disabled"))
    with pytest.raises(QuotaError):
        run_stt(eleven, stt_client(QuotaError()), tmp_path, capsys)


def test_stt_all_failing_exits_with_one_line(eleven, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(eleven, "whisper_stt", broken("model download failed"))
    with pytest.raises(SystemExit) as e:
        run_stt(eleven, stt_client(QuotaError()), tmp_path, capsys)
    msg = str(e.value)
    assert msg.startswith("stt failed: ElevenLabs:") and "faster-whisper: RuntimeError: model download failed" in msg
    assert "\n" not in msg


def test_whisper_stt_settings_and_language_codes(eleven, monkeypatch):
    seen = {}

    class Model:
        def __init__(self, name, **k):
            seen.update(name=name, **k)

        def transcribe(self, audio, **k):
            seen.update(k)
            segs = [types.SimpleNamespace(text=" Kolik je"), types.SimpleNamespace(text=" hodin? ")]
            return iter(segs), types.SimpleNamespace(language="cs")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(eleven, "decode16k", lambda p: "samples")
    monkeypatch.setenv("STT_FALLBACK_MODEL", "small")
    monkeypatch.setenv("STT_FALLBACK_LANGUAGE", "cs")
    assert eleven.whisper_stt("x.m4a") == ("Kolik je hodin?", "ces")
    assert seen["name"] == "small" and seen["language"] == "cs" and seen["compute_type"] == "int8"
    assert seen["cpu_threads"] == 4
    monkeypatch.delenv("STT_FALLBACK_LANGUAGE")
    eleven.whisper_stt("x.m4a")
    assert seen["language"] is None


# --- cooldowns: skip a remote engine that has just failed for a lasting reason ---

class ConnectError(Exception):
    """Named like httpx's, which is what the ElevenLabs SDK raises when offline."""


def counting_client(fail):
    calls = []

    def convert(**k):
        calls.append(k)
        raise fail
    return types.SimpleNamespace(text_to_speech=types.SimpleNamespace(convert=convert),
                                 speech_to_text=types.SimpleNamespace(convert=convert)), calls


def quiet_fallback(eleven, monkeypatch):
    monkeypatch.setattr(eleven, "edge_tts", lambda t, o: Path(o).write_bytes(b"x"))


def test_quota_error_cools_elevenlabs_down(eleven, monkeypatch, tmp_path, capsys):
    quiet_fallback(eleven, monkeypatch)
    cl, calls = counting_client(QuotaError())
    run_tts(eleven, monkeypatch, cl, "Hello", tmp_path / "a.mp3")
    run_tts(eleven, monkeypatch, cl, "Hello", tmp_path / "b.mp3")
    assert len(calls) == 1
    err = capsys.readouterr().err
    assert "ElevenLabs: skipped, cooling down" in err and "after quota_exceeded" in err
    entry = json.loads(Path(eleven.HEALTH).read_text())["elevenlabs"]
    assert entry["kind"] == "quota" and 890 < entry["until"] - eleven.time.time() <= 900


def test_network_error_cools_down_briefly(eleven, monkeypatch, tmp_path):
    quiet_fallback(eleven, monkeypatch)
    cl, calls = counting_client(ConnectError("Name or service not known"))
    run_tts(eleven, monkeypatch, cl, "Hello", tmp_path / "a.mp3")
    entry = json.loads(Path(eleven.HEALTH).read_text())["elevenlabs"]
    assert entry["kind"] == "network" and entry["until"] - eleven.time.time() <= 120


def test_other_errors_do_not_cool_down(eleven, monkeypatch, tmp_path):
    quiet_fallback(eleven, monkeypatch)
    cl, calls = counting_client(ValueError("bad voice id"))
    run_tts(eleven, monkeypatch, cl, "Hello", tmp_path / "a.mp3")
    run_tts(eleven, monkeypatch, cl, "Hello", tmp_path / "b.mp3")
    assert len(calls) == 2 and eleven.load_health() == {}


def test_cooldown_expires_and_success_clears_it(eleven, monkeypatch, tmp_path):
    eleven.save_health({"elevenlabs": {"until": eleven.time.time() - 1, "kind": "quota", "reason": "quota_exceeded"}})
    run_tts(eleven, monkeypatch, client(), "Hello", tmp_path / "a.mp3")
    assert (tmp_path / "a.mp3").read_bytes() == b"ID3mp3" and eleven.load_health() == {}


def test_cooldowns_can_be_turned_off(eleven, monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_COOLDOWN", "0")
    quiet_fallback(eleven, monkeypatch)
    cl, calls = counting_client(QuotaError())
    run_tts(eleven, monkeypatch, cl, "Hello", tmp_path / "a.mp3")
    run_tts(eleven, monkeypatch, cl, "Hello", tmp_path / "b.mp3")
    assert len(calls) == 2 and not Path(eleven.HEALTH).exists()


def test_stt_skips_cooling_elevenlabs_without_calling_it(eleven, monkeypatch, tmp_path, capsys):
    eleven.save_health({"elevenlabs": {"until": eleven.time.time() + 600, "kind": "quota", "reason": "quota_exceeded"}})
    cl, calls = counting_client(AssertionError("must not be called"))
    monkeypatch.setattr(eleven, "whisper_stt", lambda p: ("Ahoj", "ces"))
    r = run_stt(eleven, cl, tmp_path, capsys)
    assert calls == [] and json.loads(r.out)["text"] == "Ahoj" and "skipped, cooling down" in r.err


def test_edge_network_failure_cools_edge_down(eleven):
    def offline():
        raise TimeoutError()
    with pytest.raises(TimeoutError):
        eleven.remote("edge-tts", offline)
    with pytest.raises(eleven.Cooling, match="after TimeoutError"):
        eleven.remote("edge-tts", lambda: pytest.fail("cooling"))


def test_corrupt_state_counts_as_healthy(eleven):
    Path(eleven.HEALTH).parent.mkdir(parents=True, exist_ok=True)
    Path(eleven.HEALTH).write_text("{not json")
    assert eleven.cooling("elevenlabs") is None
    assert eleven.remote("elevenlabs", lambda: 7) == 7


def test_health_lists_and_resets(eleven, capsys):
    eleven.health([])
    assert "no engine is cooling down" in capsys.readouterr().out
    eleven.save_health({"elevenlabs": {"until": eleven.time.time() + 300, "kind": "quota",
                                       "reason": "quota_exceeded", "at": "2026-11-26 16:00:00"}})
    eleven.health([])
    assert "elevenlabs: quota (quota_exceeded)" in capsys.readouterr().out
    eleven.health(["--reset"])
    assert eleven.load_health() == {}


def test_lazy_client_imports_the_sdk_only_when_used(eleven, monkeypatch):
    built = []

    class SDK:
        def __init__(self, **k):
            built.append(k)
            self.text_to_speech = "tts-api"

    monkeypatch.setitem(sys.modules, "elevenlabs", types.SimpleNamespace(ElevenLabs=SDK))
    monkeypatch.setenv("ELEVENLABS_TIMEOUT", "7")
    lazy = eleven.LazyClient()
    assert built == []
    assert lazy.text_to_speech == "tts-api" and lazy.text_to_speech == "tts-api"
    assert built == [{"timeout": 7.0}]
