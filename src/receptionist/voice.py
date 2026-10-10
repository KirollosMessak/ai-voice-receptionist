"""Speech in and out, both on Groq.

- Speech-to-text: Whisper (large-v3-turbo), which handles Arabic and English.
- Text-to-speech: Orpheus voices (English, and Saudi Arabic). These need a one-time terms acceptance
  in the Groq console; until then synthesize() returns None and the app falls back to the browser's voice.
"""

import io
import re

from groq import Groq

STT_MODEL = "whisper-large-v3-turbo"
TTS_VOICES = {
    "en": ("canopylabs/orpheus-v1-english", "troy"),
    "ar": ("canopylabs/orpheus-arabic-saudi", "fahad"),
}
ARABIC = re.compile(r"[؀-ۿ]")


def language_of(text: str) -> str:
    return "ar" if ARABIC.search(text or "") else "en"


def transcribe(audio: bytes, client: Groq | None = None, filename: str = "speech.wav") -> str:
    client = client or Groq()
    result = client.audio.transcriptions.create(file=(filename, io.BytesIO(audio)), model=STT_MODEL)
    return result.text.strip()


def synthesize(text: str, client: Groq | None = None) -> bytes | None:
    """WAV audio of the text in a matching voice, or None if TTS isn't available (e.g. terms not accepted)."""
    model, voice = TTS_VOICES[language_of(text)]
    try:
        client = client or Groq()
        response = client.audio.speech.create(model=model, voice=voice, input=text, response_format="wav")
        return response.read()
    except Exception:
        return None
