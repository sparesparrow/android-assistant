#!/root/mia-venv/bin/python
"""ElevenLabs speech-to-text and text-to-speech for the voice assistant (official Python SDK).

  eleven.py stt <audio-file>          -> JSON {"text": ..., "language_code": ...} on stdout
  eleven.py tts <out.mp3> < text      -> writes the mp3

If ElevenLabs TTS fails (quota used up, bad key, no network), tts falls back, and says which
engine spoke in one line on stderr:
  1. Microsoft Edge neural voices (the edge-tts package; free, no key, online, best effort, the
     text goes to Microsoft): EDGE_TTS_VOICE_CS (default cs-CZ-AntoninNeural) for text with
     Czech letters, else EDGE_TTS_VOICE_EN (default en-US-AndrewMultilingualNeural). TTS_EDGE=0
     skips it.
  2. espeak-ng, offline (TTS_FALLBACK_VOICE forces a voice), encoded to mp3 by SoX.
TTS_OFFLINE_FALLBACK=0 turns all fallbacks off. Speech-to-text has no fallback.

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


def stt(client, path):
    with open(path, "rb") as f:
        r = client.speech_to_text.convert(file=f, model_id=STT_MODEL, tag_audio_events=False)
    print(json.dumps({"text": (r.text or "").strip(), "language_code": r.language_code}, ensure_ascii=False))


CZECH = re.compile(r"[ěščřžůťďňýĚŠČŘŽŮŤĎŇÝ]")  # letters other common languages lack


def offline_voice(text):
    return os.environ.get("TTS_FALLBACK_VOICE") or ("cs" if CZECH.search(text) else "en")


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
    for name, engine, voice in (("edge-tts", edge_tts, edge_voice), ("espeak-ng", offline_tts, offline_voice)):
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
