#!/root/mia-venv/bin/python
"""ElevenLabs speech-to-text and text-to-speech for the voice assistant (official Python SDK).

  eleven.py stt <audio-file>          -> JSON {"text": ..., "language_code": ...} on stdout
  eleven.py tts <out.mp3> < text      -> writes the mp3

If ElevenLabs TTS fails (quota used up, bad key, no network), tts falls back, and says which
engine spoke in one line on stderr:
  1. The phone's own Android TTS engine (Google, offline voices), through android-say.sh and the
     Tasker task "Claude Say". It speaks on the phone directly and waits until it has finished;
     `out` then gets a short silent mp3, so callers that play `out` stay as they are.
     Tasker speaks in the phone's TTS language, so when the text is in another language
     (Czech letters or not), the Edge voice goes first and the phone comes second.
     TTS_ANDROID=0 skips it. ANDROID_TTS_LANG overrides the phone's language (cs, en, ...).
  2. Microsoft Edge neural voices (the edge-tts package; free, no key, online, best effort, the
     text goes to Microsoft): EDGE_TTS_VOICE_CS (default cs-CZ-AntoninNeural) for text with
     Czech letters, else EDGE_TTS_VOICE_EN (default en-US-AndrewMultilingualNeural). TTS_EDGE=0
     skips it.
  3. espeak-ng, offline (TTS_FALLBACK_VOICE forces a voice), encoded to mp3 by SoX.
TTS_OFFLINE_FALLBACK=0 turns all fallbacks off.

If ElevenLabs STT fails, stt transcribes offline with faster-whisper (CTranslate2, int8 on the
CPU; the model is downloaded from Hugging Face once, then cached) and says so on stderr:
STT_FALLBACK_MODEL (default base; small is more accurate and about 2.5x slower),
STT_FALLBACK_LANGUAGE (default: detect; e.g. cs or en saves about 2 s), STT_FALLBACK_THREADS
(default 4). STT_OFFLINE_FALLBACK=0 turns it off.

The SDK reads ELEVENLABS_API_KEY from the environment. If it is not set, the key is loaded
from ELEVENLABS_ENV_FILE (default /root/mia/apps/rpi-backend/.env.local). Never printed.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from dotenv import load_dotenv

if not os.environ.get("ELEVENLABS_API_KEY"):
    load_dotenv(os.environ.get("ELEVENLABS_ENV_FILE", "/root/mia/apps/rpi-backend/.env.local"))

from elevenlabs import ElevenLabs  # noqa: E402  (after the key is loaded)

STT_MODEL = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2")
TTS_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")


HERE = os.path.dirname(os.path.abspath(__file__))
ISO639_3 = {"cs": "ces", "en": "eng", "sk": "slk", "de": "deu", "pl": "pol", "fr": "fra", "es": "spa"}


def stt(client, path):
    try:
        text, lang = eleven_stt(client, path)
    except Exception as e:
        if os.environ.get("STT_OFFLINE_FALLBACK", "1") == "0":
            raise
        try:
            text, lang = whisper_stt(path)
        except Exception as w:
            sys.exit(f"stt failed: ElevenLabs: {describe(e)[:200]}; faster-whisper: {type(w).__name__}: {str(w)[:160]}")
        print(f"stt: transcribed with faster-whisper ({whisper_model()}) after ElevenLabs: {describe(e)[:200]}",
              file=sys.stderr)
    print(json.dumps({"text": text, "language_code": lang}, ensure_ascii=False))


def eleven_stt(client, path):
    with open(path, "rb") as f:
        r = client.speech_to_text.convert(file=f, model_id=STT_MODEL, tag_audio_events=False)
    return (r.text or "").strip(), r.language_code


def whisper_model():
    return os.environ.get("STT_FALLBACK_MODEL", "base")


def decode16k(path):
    """Any audio SoX reads -> mono 16 kHz float32 samples (SoX, not PyAV: no codec surprises)."""
    import numpy as np

    raw = subprocess.run(["sox", path, "-t", "f32", "-r", "16000", "-c", "1", "-"],
                         capture_output=True, check=True, timeout=60).stdout
    return np.frombuffer(raw, dtype=np.float32)


def whisper_stt(path):
    """Offline speech-to-text with faster-whisper -> (text, ISO 639-3 language). Raises on failure."""
    from faster_whisper import WhisperModel  # optional dependency

    model = WhisperModel(whisper_model(), device="cpu", compute_type="int8",
                         cpu_threads=int(os.environ.get("STT_FALLBACK_THREADS", "4")))
    segments, info = model.transcribe(decode16k(path), beam_size=1, without_timestamps=True,
                                      condition_on_previous_text=False,
                                      language=os.environ.get("STT_FALLBACK_LANGUAGE") or None)
    text = "".join(s.text for s in segments).strip()
    return text, ISO639_3.get(info.language, info.language)


CZECH = re.compile(r"[ěščřžůťďňýĚŠČŘŽŮŤĎŇÝ]")  # letters other common languages lack


def offline_voice(text):
    return os.environ.get("TTS_FALLBACK_VOICE") or text_lang(text)


def edge_voice(text):
    if CZECH.search(text):
        return os.environ.get("EDGE_TTS_VOICE_CS", "cs-CZ-AntoninNeural")
    return os.environ.get("EDGE_TTS_VOICE_EN", "en-US-AndrewMultilingualNeural")


def edge_tts(text, out):
    """Microsoft Edge neural TTS (edge-tts) -> mp3 at `out` (via out.part). Raises on failure."""
    if os.environ.get("TTS_EDGE", "1") == "0":
        raise RuntimeError("turned off (TTS_EDGE=0)")
    import asyncio

    import edge_tts as edge  # optional dependency

    tmp = out + ".part"
    asyncio.run(asyncio.wait_for(edge.Communicate(text, edge_voice(text)).save(tmp), timeout=45))
    if os.path.getsize(tmp) == 0:
        raise RuntimeError("edge-tts returned no audio")
    os.replace(tmp, out)


def silent_mp3(out):
    """A 50 ms silent mp3 at `out` (via out.part), for callers that play `out` after tts."""
    tmp = out + ".part"
    subprocess.run(["sox", "-n", "-r", "22050", "-c", "1", "-t", "mp3", tmp, "trim", "0", "0.05"],
                   check=True, capture_output=True, timeout=30)
    os.replace(tmp, out)


def android_tts(text, out):
    """Speak on the phone with Android's TTS engine (android-say.sh --wait); `out` gets silence."""
    if os.environ.get("TTS_ANDROID", "1") == "0":
        raise RuntimeError("turned off (TTS_ANDROID=0)")
    if not (shutil.which("adb") and shutil.which("sox")):
        raise RuntimeError("adb or sox is not installed")
    r = subprocess.run([os.path.join(HERE, "android-say.sh"), "--wait"], input=text, text=True,
                       capture_output=True, timeout=30 + len(text) // 5)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[-1])
    silent_mp3(out)


def text_lang(text):
    return "cs" if CZECH.search(text) else "en"


def android_tts_lang():
    """The phone's TTS language (e.g. cs from com.google.android.tts:cs_CZ); None if unknown."""
    if os.environ.get("ANDROID_TTS_LANG"):
        return os.environ["ANDROID_TTS_LANG"]
    try:
        r = subprocess.run(["adb", "-s", os.environ.get("DROID_SERIAL", "127.0.0.1:5555"), "shell",
                            "settings", "get", "secure", "tts_default_locale"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    m = re.search(r"(?:^|:)([a-z]{2,3})[_-]", r.stdout.strip())
    return m.group(1) if m else None


def android_voice(text):
    return "Android default engine"


def fallback_chain(text):
    """(name, engine, voice) in the order to try: the phone first when it speaks the text's language."""
    android = ("android-say", android_tts, android_voice)
    edge = ("edge-tts", edge_tts, edge_voice)
    first = [android, edge] if android_tts_lang() in (None, text_lang(text)) else [edge, android]
    return first + [("espeak-ng", offline_tts, offline_voice)]


def offline_tts(text, out):
    """espeak-ng -> wav -> SoX -> mp3 at `out` (via out.part). Raises on failure."""
    if not (shutil.which("espeak-ng") and shutil.which("sox")):
        raise RuntimeError("espeak-ng or sox is not installed")
    with tempfile.TemporaryDirectory(prefix="tts-") as d:
        wav = os.path.join(d, "say.wav")
        subprocess.run(["espeak-ng", "-v", offline_voice(text), "-s", "165", "-w", wav, "--stdin"],
                       input=text, text=True, check=True, capture_output=True, timeout=60)
        tmp = out + ".part"
        subprocess.run(["sox", wav, "-t", "mp3", tmp], check=True, capture_output=True, timeout=60)
    os.replace(tmp, out)


def describe(e):
    """One line about an API error, no key material."""
    status = getattr(e, "status_code", None)
    body = getattr(e, "body", None)
    detail = json.dumps(body, ensure_ascii=False)[:400] if body is not None else str(e)[:300]
    return f"{type(e).__name__} status={status}: {detail}"


def tts(client, out):
    text = sys.stdin.read().strip()
    if not text:
        sys.exit("tts: empty text")
    try:
        eleven_tts(client, text, out)
        return
    except Exception as e:
        if os.environ.get("TTS_OFFLINE_FALLBACK", "1") == "0":
            raise
        failed = [f"ElevenLabs: {describe(e)[:200]}"]
    for name, engine, voice in fallback_chain(text):
        try:
            engine(text, out)
        except Exception as e:
            failed.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
            continue
        print(f"tts: spoke with {name} ({voice(text)}) after {'; '.join(failed)}"[:600], file=sys.stderr)
        return
    sys.exit(("tts failed: " + "; ".join(failed))[:600])


def eleven_tts(client, text, out):
    audio = client.text_to_speech.convert(
        text=text, voice_id=VOICE_ID, model_id=TTS_MODEL, output_format="mp3_44100_128"
    )
    tmp = out + ".part"
    with open(tmp, "wb") as f:
        for chunk in audio:
            f.write(chunk)
    os.replace(tmp, out)


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("stt", "tts"):
        sys.exit(__doc__)
    client = ElevenLabs()
    try:
        (stt if sys.argv[1] == "stt" else tts)(client, sys.argv[2])
    except Exception as e:  # API errors: one line on stderr, no key material
        sys.exit(f"{sys.argv[1]} failed: {describe(e)}")


if __name__ == "__main__":
    main()
