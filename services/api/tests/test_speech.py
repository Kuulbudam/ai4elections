"""Speech to text: Groq Whisper first, Gemini (audio sent inline) as the backup."""

from __future__ import annotations

import base64

import httpx
import pytest

from app.assistant import llm, speech

from .test_providers import _clean_env  # noqa: F401  (clears provider settings for every test)


def test_no_key_no_voice():
    assert speech.configured() is None


def test_whisper_gets_the_language_hint_and_pidgin_is_sent_as_english(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "gsk-test")
    sent = []

    def fake_post(url, headers, data, files, timeout):
        sent.append((url, data, files["file"][0], files["file"][2]))
        return httpx.Response(200, json={"text": "  Wane LGA ne ke da mafi yawan matsaloli?  "})

    monkeypatch.setattr(speech.httpx, "post", fake_post)
    t = speech.configured()
    assert isinstance(t, speech.Whisper) and t.name == "whisper-large-v3"
    assert t.transcribe(b"ogg", "audio/ogg", "ha") == "Wane LGA ne ke da mafi yawan matsaloli?"
    t.transcribe(b"x", "audio/webm", "pcm")
    t.transcribe(b"x", "audio/mp4", "ig")
    assert sent[0][0].endswith("/audio/transcriptions") and sent[0][1]["language"] == "ha"
    assert sent[0][2:] == ("speech.ogg", "audio/ogg")
    assert sent[1][1]["language"] == "en" and "language" not in sent[2][1] and sent[2][2] == "speech.mp4"


def test_switches_to_gemini_when_whisper_is_busy(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "gsk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setattr(llm, "list_models", lambda k, b: ["gemini-2.5-flash"] if "generativelanguage" in b else [])
    calls = []

    def fake_post(url, headers, timeout, data=None, files=None, json=None):
        calls.append(url)
        if "audio/transcriptions" in url:
            return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}})
        part = json["contents"][0]["parts"][1]["inline_data"]
        assert part["mime_type"] == "audio/webm" and base64.b64decode(part["data"]) == b"clip"
        assert "Yoruba" in json["contents"][0]["parts"][0]["text"] and headers["x-goog-api-key"] == "g-test"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Ìjọba ìbílẹ̀ wo?"}]}}]})

    monkeypatch.setattr(speech.httpx, "post", fake_post)
    t = speech.configured()
    assert t.transcribe(b"clip", "audio/webm", "yo") == "Ìjọba ìbílẹ̀ wo?"
    assert t.name == "gemini-2.5-flash"
    assert calls[1].endswith("/v1beta/models/gemini-2.5-flash:generateContent")


def test_every_provider_failing_raises_a_plain_error_without_the_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "gsk-secret")
    monkeypatch.setattr(
        speech.httpx, "post", lambda *a, **k: httpx.Response(400, json={"error": {"code": "invalid_file"}})
    )
    with pytest.raises(llm.LLMError) as e:
        speech.configured().transcribe(b"x", "audio/webm", "en")
    assert "invalid_file" in str(e.value) and "gsk-secret" not in str(e.value)
