"""Two AI providers: Groq first, Google Gemini as the free backup (or the other way round)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.assistant import llm
from app.sheets import read as rd
from app.sheets.read import read_sheet

REAL_LIST_MODELS = llm.list_models
GOOD = {"legible": True, "valid_votes": {"value": 319, "confidence": "high"}, "party_votes": []}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_VISION_MODEL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GEMINI_VISION_MODEL",
        "AI_PROVIDER_ORDER",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm, "list_models", lambda k, b: [])
    llm._chosen.clear()
    rd._chosen.clear()


def test_no_key_no_model():
    assert llm.configured_model() is None and rd.configured_vision_model() is None


def test_gemini_alone_uses_the_gemini_endpoint_and_a_free_flash_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    m = llm.configured_model()
    assert isinstance(m, llm.OpenAICompatible) and m.base_url == llm.GEMINI_BASE_URL
    assert m.name == llm.PREFERRED_GEMINI_MODELS[0]
    v = rd.configured_vision_model()
    assert v.base_url == llm.GEMINI_BASE_URL and v.name == llm.PREFERRED_GEMINI_MODELS[0]


def test_both_keys_give_groq_first_then_gemini_and_order_can_be_swapped(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "q")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    m = llm.configured_model()
    assert isinstance(m, llm.FallbackChat)
    assert [x.base_url for x in m.models] == [llm.DEFAULT_BASE_URL, llm.GEMINI_BASE_URL]
    v = rd.configured_vision_model()
    assert isinstance(v, rd.FallbackVision) and v.models[0].wait_on_limit is False and v.models[1].wait_on_limit
    monkeypatch.setenv("AI_PROVIDER_ORDER", "gemini, groq")
    assert [x.base_url for x in llm.configured_model().models] == [llm.GEMINI_BASE_URL, llm.DEFAULT_BASE_URL]


def test_gemini_model_names_are_listed_with_a_prefix(monkeypatch):
    monkeypatch.setattr(llm, "list_models", REAL_LIST_MODELS)
    monkeypatch.setattr(
        llm.httpx, "get",
        lambda *a, **k: httpx.Response(200, json={"data": [{"id": "models/gemini-3.6-flash"}, {"id": "models/x"}]},
                                       request=httpx.Request("GET", "https://g")),
    )  # fmt: skip
    assert llm.list_models("g", llm.GEMINI_BASE_URL) == ["gemini-3.6-flash", "x"]
    assert llm.choose_model("g", llm.GEMINI_BASE_URL) == "gemini-3.6-flash"


def test_chat_switches_to_the_backup_when_the_first_is_busy_and_stays_there(monkeypatch):
    sent = []

    def fake_post(url, headers, json, timeout):
        sent.append((url, json))
        if "groq" in url:
            return httpx.Response(429, json={"error": {"message": "Rate limit reached on tokens per day (TPD)"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    m = llm.FallbackChat([llm.OpenAICompatible("q", llm.DEFAULT_BASE_URL, "openai/gpt-oss-120b"),
                          llm.OpenAICompatible("g", llm.GEMINI_BASE_URL, "gemini-3.8-flash")])  # fmt: skip
    assert m.chat([{"role": "user", "content": "hi"}], [])["content"] == "ok"
    assert m.name == "gemini-3.8-flash"
    m.chat([{"role": "user", "content": "again"}], [])
    assert [u for u, _ in sent].count(llm.DEFAULT_BASE_URL + "/chat/completions") == 1  # not retried every round
    assert sent[1][1]["reasoning_effort"] == "low"


def test_chat_raises_when_every_provider_fails_and_malformed_tool_calls_do_not_switch(monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: httpx.Response(503, json=[{"error": {
        "code": 503, "status": "UNAVAILABLE", "message": "The model is overloaded."}}]))  # fmt: skip
    m = llm.FallbackChat([llm.OpenAICompatible("q", llm.DEFAULT_BASE_URL, "a"),
                          llm.OpenAICompatible("g", llm.GEMINI_BASE_URL, "b")])  # fmt: skip
    with pytest.raises(llm.LLMError) as e:
        m.chat([], [])
    assert e.value.code == "rate_limit" and "UNAVAILABLE" in str(e.value)

    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: httpx.Response(400, json={"error": {
        "code": "tool_use_failed"}}))  # fmt: skip
    m = llm.FallbackChat([llm.OpenAICompatible("q", llm.DEFAULT_BASE_URL, "a"),
                          llm.OpenAICompatible("g", llm.GEMINI_BASE_URL, "b")])  # fmt: skip
    with pytest.raises(llm.LLMError):
        m.chat([], [])
    assert m.name == "a"  # the assistant loop corrects the model; no provider switch


def test_tool_calls_from_another_provider_get_a_placeholder_signature_and_real_ones_are_kept(monkeypatch):
    sent = []
    monkeypatch.setattr(llm.httpx, "post", lambda url, headers, json, timeout: (
        sent.append(json), httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))[1])  # fmt: skip
    real = {"google": {"thought_signature": "abc"}}
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            {"id": "2", "type": "function", "function": {"name": "t", "arguments": "{}"}, "extra_content": real}]},
        {"role": "tool", "tool_call_id": "1", "content": "{}"},
    ]  # fmt: skip
    llm.OpenAICompatible("g", llm.GEMINI_BASE_URL, "gemini-3.8-flash").chat(messages, [])
    calls = sent[0]["messages"][1]["tool_calls"]
    assert calls[0]["extra_content"]["google"]["thought_signature"] == llm.SKIP_SIGNATURE
    assert calls[1]["extra_content"] == real
    assert "extra_content" not in messages[1]["tool_calls"][0]  # the caller's history is not changed
    llm.OpenAICompatible("q", llm.DEFAULT_BASE_URL, "openai/gpt-oss-120b").chat(messages, [])
    assert "extra_content" not in sent[1]["messages"][1]["tool_calls"][0]  # Groq gets the history as it was


def test_sheet_reader_switches_without_waiting(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(url)
        if "groq" in url:
            return httpx.Response(429, headers={"retry-after": "5"}, json={"error": {"message": "busy"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": json_dumps(GOOD)}}]})

    json_dumps = json.dumps
    monkeypatch.setattr(rd.httpx, "post", fake_post)
    monkeypatch.setattr(rd.time, "sleep", lambda s: pytest.fail("should switch, not wait"))
    v = rd.FallbackVision([rd.OpenAICompatibleVision("q", llm.DEFAULT_BASE_URL, "qwen/qwen3.8-27b"),
                           rd.OpenAICompatibleVision("g", llm.GEMINI_BASE_URL, "gemini-3.8-flash")])  # fmt: skip
    out = read_sheet(v, b"\xff\xd8x")
    assert out["status"] == "read" and out["figures"]["valid_votes"] == 319 and v.name == "gemini-3.8-flash"
    assert len(calls) == 2


def test_sheet_reader_gemini_request_has_room_for_thinking(monkeypatch):
    sent = []
    monkeypatch.setattr(rd.httpx, "post", lambda url, headers, json, timeout: (
        sent.append(json),
        httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}))[1])  # fmt: skip
    rd.OpenAICompatibleVision("g", llm.GEMINI_BASE_URL, "gemini-3.8-flash").read_image("p", b"\xff\xd8x")
    assert sent[0]["max_completion_tokens"] == 8000 and sent[0]["reasoning_effort"] == "low"
    assert sent[0]["response_format"] == {"type": "json_object"}


def test_sheet_reader_all_providers_failing_falls_back_to_typing(monkeypatch):
    monkeypatch.setattr(rd.httpx, "post", lambda *a, **k: httpx.Response(401, json={"error": {"message": "bad key"}}))
    v = rd.FallbackVision([rd.OpenAICompatibleVision("q", llm.DEFAULT_BASE_URL, "a"),
                           rd.OpenAICompatibleVision("g", llm.GEMINI_BASE_URL, "b")])  # fmt: skip
    out = read_sheet(v, b"\xff\xd8x")
    assert out["status"] == "failed"
