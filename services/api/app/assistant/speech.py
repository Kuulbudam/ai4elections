"""Speech to text for voice questions and spoken field notes (Phase 18).

Groq's Whisper is tried first (LLM_API_KEY; LLM_STT_MODEL optional), then Gemini (GEMINI_API_KEY) with the audio sent
inline. Audio is passed straight through and never stored. Whisper has no Igbo or Pidgin, so Igbo is left to
auto-detect and Pidgin is treated as English; Gemini handles both.
"""

from __future__ import annotations

import base64
import os
from typing import Protocol

import httpx

from app.assistant.llm import GEMINI_BASE_URL, PREFERRED_GEMINI_MODELS, LLMError, _reason, candidate_models, providers

DEFAULT_STT_MODEL = "whisper-large-v3"
MAX_AUDIO_BYTES = 3 * 1024 * 1024  # about three minutes of phone speech; requests above 4.5 MB fail on Vercel
MIME = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4", "audio/mpeg": "mp3", "audio/wav": "wav",
        "audio/x-m4a": "m4a", "audio/aac": "aac"}  # fmt: skip
WHISPER_LANG = {"en": "en", "pcm": "en", "ha": "ha", "yo": "yo", "ig": None}
LANG_NAME = {"en": "English", "pcm": "Nigerian Pidgin", "ha": "Hausa", "yo": "Yoruba", "ig": "Igbo"}


class Transcriber(Protocol):
    name: str

    def transcribe(self, audio: bytes, mime: str, language: str) -> str: ...


class Whisper:
    def __init__(self, key: str, base: str, model: str) -> None:
        self.key, self.base, self.name = key, base.rstrip("/"), model

    def transcribe(self, audio: bytes, mime: str, language: str) -> str:
        data = {"model": self.name, "response_format": "json", "temperature": "0"}
        if lang := WHISPER_LANG.get(language):
            data["language"] = lang
        try:
            r = httpx.post(f"{self.base}/audio/transcriptions", headers={"Authorization": f"Bearer {self.key}"},
                           data=data, files={"file": (f"speech.{MIME.get(mime, 'webm')}", audio, mime)},
                           timeout=60)  # fmt: skip
        except httpx.HTTPError as e:
            raise LLMError("The speech service could not be reached.") from e
        if r.status_code >= 400:
            raise LLMError(f"The speech service refused the audio{_reason(r)}.")
        return str(r.json().get("text", "")).strip()


class GeminiAudio:
    def __init__(self, key: str, model: str) -> None:
        self.key, self.name = key, model

    def transcribe(self, audio: bytes, mime: str, language: str) -> str:
        lang = LANG_NAME.get(language, "English")
        prompt = (
            f"Write down exactly what is said in this recording. It is most likely in {lang}. Reply with the words "
            "only: no translation, notes or quotation marks. If nothing is said, reply with nothing."
        )
        clip = {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode()}}
        body = {"contents": [{"parts": [{"text": prompt}, clip]}], "generationConfig": {"temperature": 0}}
        base = GEMINI_BASE_URL.removesuffix("/openai")
        try:
            r = httpx.post(f"{base}/models/{self.name}:generateContent", headers={"x-goog-api-key": self.key},
                           json=body, timeout=60)  # fmt: skip
        except httpx.HTTPError as e:
            raise LLMError("The speech service could not be reached.") from e
        if r.status_code >= 400:
            raise LLMError(f"The speech service refused the audio{_reason(r)}.")
        parts = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        return " ".join(str(p.get("text", "")) for p in parts).strip()


class FallbackSpeech:
    def __init__(self, items: list[Transcriber]) -> None:
        self.items = items
        self.name = items[0].name

    def transcribe(self, audio: bytes, mime: str, language: str) -> str:
        last: LLMError | None = None
        for t in self.items:
            try:
                text = t.transcribe(audio, mime, language)
                self.name = t.name
                return text
            except LLMError as e:
                last = e
        raise last or LLMError("No speech service is set up.")


def configured() -> Transcriber | None:
    items: list[Transcriber] = []
    for p in providers():
        if p["provider"] == "groq":
            model = os.environ.get("LLM_STT_MODEL", "").strip() or DEFAULT_STT_MODEL
            items.append(Whisper(p["key"], p["base"], model))
        elif p["provider"] == "gemini":
            m = os.environ.get("GEMINI_STT_MODEL", "").strip()
            for name in [m] if m else candidate_models(p["key"], p["base"], PREFERRED_GEMINI_MODELS):
                items.append(GeminiAudio(p["key"], name))
    if not items:
        return None
    return items[0] if len(items) == 1 else FallbackSpeech(items)
