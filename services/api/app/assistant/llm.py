"""Chat model access. Any OpenAI-compatible chat-completions endpoint with tool calling works.

Configured by environment: LLM_API_KEY (required to enable the assistant), LLM_BASE_URL (default Groq),
LLM_MODEL. The key is read on the server only and never logged or returned.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import httpx

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "llama-3.3-70b-versatile"


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
        if r.status_code == 429:
            raise LLMError("The assistant is busy (free-tier limit reached). Try again in a minute.")
        if r.status_code in (401, 403):
            raise LLMError("The assistant is not authorised with its provider. An administrator must check the key.")
        if r.status_code >= 400:
            raise LLMError(f"The assistant service returned an error (HTTP {r.status_code}).")
        try:
            return r.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError("The assistant service returned an unexpected response.") from e


def configured_model() -> ChatModel | None:
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        return None
    return OpenAICompatible(
        key,
        os.environ.get("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL,
        os.environ.get("LLM_MODEL", "").strip() or DEFAULT_MODEL,
    )
