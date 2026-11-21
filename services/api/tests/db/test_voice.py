"""Phase 18: voice. A short recording becomes text; the audio is never stored."""

from __future__ import annotations

import base64

import pytest

from app.main import app
from app.routes import assistant as assistant_route

from .test_hierarchy import member
from .test_ingestion import H


class FakeStt:
    name = "whisper-test"

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str, str]] = []

    def transcribe(self, audio: bytes, mime: str, language: str) -> str:
        self.calls.append((audio, mime, language))
        return "" if audio == b"silence" else "Which LGA has the most incidents?"


@pytest.fixture()
def stt():
    fake = FakeStt()
    app.dependency_overrides[assistant_route.get_transcriber] = lambda: fake
    yield fake
    app.dependency_overrides.pop(assistant_route.get_transcriber, None)


def clip(api, world, uid, audio=b"opus-bytes", mime="audio/webm;codecs=opus", language="ha", project=None):
    body = {"project_id": project or world.a.rows["projects"], "audio_base64": base64.b64encode(audio).decode(),
            "mime_type": mime, "language": language}  # fmt: skip
    return api.post("/assistant/transcribe", json=body, headers=H(uid))


def test_speech_becomes_text_for_askers_and_field_agents(api, world, stt):
    r = clip(api, world, world.a.users["analyst"])
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "Which LGA has the most incidents?", "language": "ha", "model": "whisper-test"}
    assert stt.calls[-1] == (b"opus-bytes", "audio/webm", "ha")
    assert clip(api, world, member(world, "field_agent")).status_code == 200
    assert clip(api, world, world.a.users["viewer"]).status_code == 403
    assert clip(api, world, world.b.users["admin"]).status_code == 404


def test_bad_or_empty_audio_and_no_provider(api, world, stt):
    admin = world.a.users["admin"]
    assert clip(api, world, admin, audio=b"silence").status_code == 400
    assert clip(api, world, admin, audio=b"x" * (3 * 1024 * 1024 + 1)).status_code == 400
    assert clip(api, world, admin, mime="video/mp4").status_code == 400
    app.dependency_overrides[assistant_route.get_transcriber] = lambda: None
    r = clip(api, world, admin)
    assert r.status_code == 503 and "not set up" in r.json()["message"]
