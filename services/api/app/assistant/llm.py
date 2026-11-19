"""Chat model access. Any OpenAI-compatible chat-completions endpoint with tool calling works.

Configured by environment: LLM_API_KEY (required to enable the assistant), LLM_BASE_URL (default Groq),
LLM_MODEL. The key is read on the server only and never logged or returned.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import httpx

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
# Used in this order when LLM_MODEL is not set: the first one the provider currently lists (all support tool calling).
PREFERRED_MODELS = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "moonshotai/kimi-k2-instruct",
    "qwen/qwen3-32b",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "openai/gpt-oss-20b",
]
_chosen: dict[str, str] = {}


class LLMError(Exception):
    """The model service failed; the message is safe to show (no key, no prompt)."""


class ChatModel(Protocol):
    name: str

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Return the assistant message: {"content": str|None, "tool_calls": [...]}."""
        ...


class OpenAICompatible:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 45.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = model
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._timeout = timeout

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        body = {"model": self.name, "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0}
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", headers=self._headers, json=body, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise LLMError("The assistant service could not be reached. Try again shortly.") from e
        if r.status_code == 404:
            _chosen.pop(self.base_url, None)  # pick again next time
            raise LLMError(f"The assistant's model ({self.name}) is not available from the provider.")
        if r.status_code == 429:
            raise LLMError("The assistant is busy (free-tier limit reached). Try again in a minute.")
        if r.status_code in (401, 403):
            raise LLMError("The assistant is not authorised with its provider. An administrator must check the key.")
        if r.status_code >= 400:
            raise LLMError(f"The assistant service returned an error (HTTP {r.status_code}{_reason(r)}).")
        try:
            return r.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError("The assistant service returned an unexpected response.") from e


def _reason(r: httpx.Response) -> str:
    """The provider's error code (never the prompt, never the key)."""
    try:
        e = r.json().get("error", {})
        code = e.get("code") or e.get("type")
        return f": {str(code)[:60]}" if code else ""
    except ValueError:
        return ""


def list_models(api_key: str, base_url: str) -> list[str]:
    """Model ids the provider lists for this key (empty on any failure)."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []) if m.get("active", True))
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return []


def choose_model(api_key: str, base_url: str) -> str:
    if base_url in _chosen:
        return _chosen[base_url]
    listed = set(list_models(api_key, base_url))
    model = next((m for m in PREFERRED_MODELS if m in listed), PREFERRED_MODELS[0])
    if listed:
        _chosen[base_url] = model
    return model


def configured_model() -> ChatModel | None:
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        return None
    base = os.environ.get("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL
    model = os.environ.get("LLM_MODEL", "").strip() or choose_model(key, base)
    return OpenAICompatible(key, base, model)
