"""Chat model access. Any OpenAI-compatible chat-completions endpoint with tool calling works.

Two free providers can be set up; when the first is busy, out of quota or down, the next one answers:
* Groq: LLM_API_KEY (LLM_BASE_URL, LLM_MODEL optional);
* Google Gemini: GEMINI_API_KEY (GEMINI_MODEL optional), through Gemini's OpenAI-compatible endpoint.
AI_PROVIDER_ORDER (default "groq,gemini") sets which is tried first. Keys are read on the server only and are
never logged or returned.
"""

from __future__ import annotations

import os
import re
from typing import Any, Protocol

import httpx

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
# Free-tier Gemini models (Flash models read images too), in order of preference.
# The newest Flash models are often "UNAVAILABLE" (overloaded) on the free tier, so up to GEMINI_TRIES of the listed
# ones are tried in turn, ending with the older, steadier 2.5 Flash.
PREFERRED_GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3-flash-preview", "gemini-2.5-flash",
                           "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]  # fmt: skip
GEMINI_TRIES = 3
# Gemini 3 checks a "thought signature" on earlier tool calls. Calls it did not make itself (e.g. made by the other
# provider before a switch) get Google's documented placeholder.
SKIP_SIGNATURE = "skip_thought_signature_validator"
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
MAX_REPLY_TOKENS = 1200  # providers count the requested maximum against the per-minute token budget


class LLMError(Exception):
    """The model service failed; the message is safe to show (no key, no prompt)."""

    def __init__(self, message: str, code: str | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


class ChatModel(Protocol):
    name: str

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], temperature: float = 0
    ) -> dict[str, Any]:
        """Return the assistant message: {"content": str|None, "tool_calls": [...]}."""
        ...


class OpenAICompatible:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 45.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = model
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._timeout = timeout

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], temperature: float = 0
    ) -> dict[str, Any]:
        gemini = is_gemini(self.base_url)
        if gemini:
            messages = with_signatures(messages)
        body: dict[str, Any] = {"model": self.name, "messages": messages, "tools": tools, "tool_choice": "auto",
                                "temperature": temperature,
                                "max_completion_tokens": MAX_REPLY_TOKENS * (4 if gemini else 1)}  # fmt: skip
        if "gpt-oss" in self.name or gemini:
            body["reasoning_effort"] = "low"  # fewer hidden reasoning tokens against the per-minute budget
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", headers=self._headers, json=body, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise LLMError("The assistant service could not be reached. Try again shortly.") from e
        if r.status_code == 404:
            _chosen.pop(self.base_url, None)  # pick again next time
            _candidates.pop(self.base_url, None)
            raise LLMError(f"The assistant's model ({self.name}) is not available from the provider.")
        if r.status_code in (429, 503):  # 503: Gemini's "model overloaded"
            try:
                wait = float(r.headers.get("retry-after", ""))
            except ValueError:
                wait = None
            raise LLMError(f"The assistant is busy (free-tier limit reached{_limit_detail(r)}). Try again in a minute.",
                           code="rate_limit", retry_after=wait)  # fmt: skip
        if r.status_code in (401, 403):
            raise LLMError("The assistant is not authorised with its provider. An administrator must check the key.")
        if r.status_code >= 400:
            reason = _reason(r)
            raise LLMError(f"The assistant service returned an error (HTTP {r.status_code}{reason}).",
                           code=reason.lstrip(": ") or None)  # fmt: skip
        try:
            return r.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError("The assistant service returned an unexpected response.") from e


def provider_error(r: httpx.Response) -> dict[str, Any]:
    """The provider's error object. Groq sends {"error": {...}}; Gemini may send [{"error": {...}}]."""
    try:
        body = r.json()
    except ValueError:
        return {}
    if isinstance(body, list) and body:
        body = body[0]
    e = body.get("error") if isinstance(body, dict) else None
    return e if isinstance(e, dict) else {}


def _limit_detail(r: httpx.Response) -> str:
    """e.g. 'Limit 8000, Used 6100, Requested 2400' from the provider's 429 message (no prompt content)."""
    m = re.search(r"Limit \d+, Used \d+, Requested \d+", str(provider_error(r).get("message", "")))
    if m:
        return f": {m.group(0)}"
    status = provider_error(r).get("status")  # Gemini: RESOURCE_EXHAUSTED / UNAVAILABLE
    return f": {str(status)[:40]}" if status else ""


def _reason(r: httpx.Response) -> str:
    """The provider's error code (never the prompt, never the key)."""
    e = provider_error(r)
    code = e.get("code") or e.get("type") or e.get("status")
    return f": {str(code)[:60]}" if code else ""


def is_gemini(base_url: str) -> bool:
    return "generativelanguage.googleapis.com" in base_url


def with_signatures(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A copy where every earlier tool call carries a thought signature (Gemini 3 rejects one without)."""
    out = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                if not (tc.get("extra_content") or {}).get("google", {}).get("thought_signature"):
                    tc = tc | {"extra_content": {"google": {"thought_signature": SKIP_SIGNATURE}}}
                calls.append(tc)
            m = m | {"tool_calls": calls}
        out.append(m)
    return out


class FallbackChat:
    """Tries each provider in order; once one fails (busy, out of quota, down), the next answers for the rest
    of the question. Only when every provider fails is the last error raised."""

    def __init__(self, models: list[ChatModel]) -> None:
        self.models = models
        self._i = 0

    @property
    def name(self) -> str:
        return self.models[self._i].name

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], temperature: float = 0
    ) -> dict[str, Any]:
        while True:
            try:
                return self.models[self._i].chat(messages, tools, temperature)
            except LLMError as e:
                # a malformed tool call is the model's own mistake: the assistant loop corrects it on the same model
                if e.code == "tool_use_failed" or self._i == len(self.models) - 1:
                    raise
                self._i += 1


def list_models(api_key: str, base_url: str) -> list[str]:
    """Model ids the provider lists for this key (empty on any failure)."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        r.raise_for_status()
        # Gemini lists ids as "models/gemini-..."; requests use the bare name
        return sorted(m["id"].removeprefix("models/") for m in r.json().get("data", []) if m.get("active", True))
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return []


def choose_model(api_key: str, base_url: str, preferred: list[str] | None = None) -> str:
    if base_url in _chosen:
        return _chosen[base_url]
    preferred = preferred or (PREFERRED_GEMINI_MODELS if is_gemini(base_url) else PREFERRED_MODELS)
    listed = set(list_models(api_key, base_url))
    model = next((m for m in preferred if m in listed), preferred[0])
    if listed:
        _chosen[base_url] = model
    return model


_candidates: dict[str, list[str]] = {}


def candidate_models(api_key: str, base_url: str, preferred: list[str], limit: int = GEMINI_TRIES) -> list[str]:
    """Up to `limit` preferred models the provider lists, in order, to be tried one after another.
    If the list cannot be read: the first preference only (not cached, so the list is read again next time)."""
    if base_url in _candidates:
        return _candidates[base_url]
    listed = set(list_models(api_key, base_url))
    if not listed:
        return preferred[:1]
    found = [m for m in preferred if m in listed][:limit] or preferred[:1]
    _candidates[base_url] = found
    return found


def provider_models(p: dict[str, str]) -> list[str]:
    """The chat models to try for one provider: the one set by environment, else Gemini's candidates, else the
    first preferred model the provider lists."""
    if p["model"]:
        return [p["model"]]
    if is_gemini(p["base"]):
        return candidate_models(p["key"], p["base"], PREFERRED_GEMINI_MODELS)
    return [choose_model(p["key"], p["base"])]


def providers() -> list[dict[str, str]]:
    """The providers that have a key, in AI_PROVIDER_ORDER: {"provider", "key", "base", "model", "vision_model"}
    (model names empty when not set by environment, to be chosen from the provider's list)."""
    found: dict[str, dict[str, str]] = {}
    if key := os.environ.get("LLM_API_KEY", "").strip():
        found["groq"] = {"provider": "groq", "key": key,
                         "base": os.environ.get("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL,
                         "model": os.environ.get("LLM_MODEL", "").strip(),
                         "vision_model": os.environ.get("LLM_VISION_MODEL", "").strip()}  # fmt: skip
    if key := os.environ.get("GEMINI_API_KEY", "").strip():
        found["gemini"] = {"provider": "gemini", "key": key, "base": GEMINI_BASE_URL,
                           "model": os.environ.get("GEMINI_MODEL", "").strip(),
                           "vision_model": os.environ.get("GEMINI_VISION_MODEL", "").strip()}  # fmt: skip
    order = [p.strip().lower() for p in os.environ.get("AI_PROVIDER_ORDER", "groq,gemini").split(",")]
    return [found[p] for p in dict.fromkeys(order + ["groq", "gemini"]) if p in found]


def configured_model() -> ChatModel | None:
    models: list[ChatModel] = [
        OpenAICompatible(p["key"], p["base"], m) for p in providers() for m in provider_models(p)
    ]
    if not models:
        return None
    return models[0] if len(models) == 1 else FallbackChat(models)
