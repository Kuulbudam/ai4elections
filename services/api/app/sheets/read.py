"""AI reading of a result-sheet photo (vision model). The output is a draft for a person to confirm.

The model is asked to copy what is written, never to calculate or guess. Its reply is treated as untrusted
text: parsed, type-checked and range-checked here. Personal names and signatures on the sheet are not read.
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Protocol

import httpx

from app.assistant.llm import DEFAULT_BASE_URL, LLMError, list_models

# Vision-capable models, in order of preference, used when LLM_VISION_MODEL is not set.
PREFERRED_VISION_MODELS = [
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
]
_chosen: dict[str, str] = {}
COUNT_FIELDS = ["registered_voters", "accredited_voters", "rejected_votes", "valid_votes", "total_cast"]
TEXT_FIELDS = {"pu_code": 64, "state": 120, "lga": 120, "ward": 120, "polling_unit_name": 200}
LEVELS = {"high", "medium", "low"}
MAX_COUNT = 10_000_000

PROMPT = """You are reading a photo of a Nigerian polling-unit election result sheet (INEC Form EC8A).
Copy what is WRITTEN on the sheet into JSON. Do not calculate, correct or guess any figure.

Return only this JSON object:
{
  "legible": true or false (false if the photo is not a result sheet or most figures cannot be read),
  "notes": "one or two short sentences about anything unclear, crossed out, or where figures and words disagree",
  "pu_code": {"value": "polling unit code as written, e.g. 31/04/01/003", "confidence": "high|medium|low"},
  "state": {"value": "...", "confidence": "..."},
  "lga": {"value": "...", "confidence": "..."},
  "ward": {"value": "...", "confidence": "..."},
  "polling_unit_name": {"value": "...", "confidence": "..."},
  "registered_voters": {"value": number, "confidence": "..."},
  "accredited_voters": {"value": number, "confidence": "..."},
  "rejected_votes": {"value": number, "confidence": "..."},
  "valid_votes": {"value": number, "confidence": "..."},
  "total_cast": {"value": number, "confidence": "..."},
  "party_votes": [{"party": "party abbreviation as printed, e.g. APC", "votes": number, "confidence": "..."}]
}
Rules:
- Use null for any value that is not written or that you cannot read. A null is better than a guess.
- "registered_voters" is the number of voters on the register; "accredited_voters" is the number accredited;
  "total_cast" is the total used ballot papers / total votes cast; "valid_votes" is the total valid votes.
- Read each figure from the digits. If the amount in words is visible and disagrees with the digits, give the
  digits, set confidence to "low" and say so in notes.
- "confidence": "high" only when the digits are clear; "medium" if slightly unclear; "low" if hard to read.
- List every party row that has a score written, in the order printed. Skip rows with no score.
- Do not copy names of people, signatures or phone numbers."""


class VisionModel(Protocol):
    name: str

    def read_image(self, prompt: str, jpeg: bytes) -> str:
        """Return the model's text reply for the prompt and image."""
        ...


class OpenAICompatibleVision:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = model
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._timeout = timeout

    def read_image(self, prompt: str, jpeg: bytes) -> str:
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        plain = {
            "model": self.name, "temperature": 0, "max_completion_tokens": 1500,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                      {"type": "image_url", "image_url": {"url": url}}]}],
        }  # fmt: skip
        tuned = plain | {"response_format": {"type": "json_object"}}
        if "qwen" in self.name:
            tuned["reasoning_effort"] = "none"  # copying figures needs no hidden "thinking" tokens
        try:
            r = httpx.post(
                f"{self.base_url}/chat/completions", headers=self._headers, json=tuned, timeout=self._timeout
            )
            if r.status_code == 400:  # an option this model does not take: once more with the plain request
                r = httpx.post(f"{self.base_url}/chat/completions", headers=self._headers, json=plain,
                               timeout=self._timeout)  # fmt: skip
        except httpx.HTTPError as e:
            raise LLMError("The sheet reader could not reach the AI service. Type the figures in instead.") from e
        if r.status_code == 404:
            _chosen.pop(self.base_url, None)
            raise LLMError(f"The sheet reader's model ({self.name}) is not available from the provider.")
        if r.status_code == 429:
            raise LLMError("The sheet reader is busy (free-tier limit reached). Try again in a minute or type the "
                           "figures in.", code="rate_limit")  # fmt: skip
        if r.status_code >= 400:
            raise LLMError(f"The sheet reader's AI service returned an error (HTTP {r.status_code}).")
        try:
            return str(r.json()["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError("The sheet reader's AI service returned an unexpected response.") from e


def choose_vision_model(api_key: str, base_url: str) -> str | None:
    if base_url in _chosen:
        return _chosen[base_url]
    listed = list_models(api_key, base_url)
    model = next((m for m in PREFERRED_VISION_MODELS if m in listed), None)
    if model is None:  # any other listed model that says it can see images
        model = next((m for m in listed if re.search(r"vision|llama-4|-vl\b|-vl-|qwen3\.[6-9]", m)), None)
    if model is None and not listed:
        model = PREFERRED_VISION_MODELS[0]  # listing failed; try the first choice anyway
    if model and listed:
        _chosen[base_url] = model
    return model


def configured_vision_model() -> VisionModel | None:
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        return None
    base = os.environ.get("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL
    model = os.environ.get("LLM_VISION_MODEL", "").strip() or choose_vision_model(key, base)
    return OpenAICompatibleVision(key, base, model) if model else None


# ----------------------------------------------------------------------------------------- parsing (untrusted)


def _count(v: Any) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        n = int(v)
        return n if float(v) == n and 0 <= n <= MAX_COUNT else None
    s = re.sub(r"[\s,._']", "", str(v))
    return int(s) if s.isdigit() and int(s) <= MAX_COUNT else None


def _level(v: Any) -> str:
    v = str(v or "").strip().lower()
    return v if v in LEVELS else "low"


def _unwrap(v: Any) -> tuple[Any, str]:
    if isinstance(v, dict):
        return v.get("value"), _level(v.get("confidence"))
    return v, "low"  # a bare value without its confidence is treated as unsure


def _text(v: Any, limit: int) -> str | None:
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s[:limit] if s and s.lower() not in {"null", "none", "n/a", "-"} else None


def _json_object(text: str) -> dict[str, Any] | None:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()  # reasoning models may show their working
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    try:
        obj = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            return None
    return obj if isinstance(obj, dict) else None


def empty_reading(status: str, notes: str | None = None) -> dict[str, Any]:
    figures: dict[str, Any] = {k: None for k in TEXT_FIELDS} | {k: None for k in COUNT_FIELDS} | {"party_votes": []}
    return {"status": status, "figures": figures, "confidence": {}, "legible": False, "notes": notes}


def parse_reading(text: str) -> dict[str, Any]:
    obj = _json_object(text)
    if obj is None:
        return empty_reading("unreadable", "The AI reply could not be understood. Type the figures in.")
    out = empty_reading("read")
    fig, conf = out["figures"], out["confidence"]
    for k, limit in TEXT_FIELDS.items():
        v, c = _unwrap(obj.get(k))
        fig[k] = _text(v, limit)
        if fig[k] is not None:
            conf[k] = c
    for k in COUNT_FIELDS:
        v, c = _unwrap(obj.get(k))
        fig[k] = _count(v)
        if fig[k] is not None:
            conf[k] = c
    seen: set[str] = set()
    for p in obj.get("party_votes") or []:
        if not isinstance(p, dict):
            continue
        name = _text(p.get("party"), 20)
        votes = _count(p.get("votes"))
        if not name or votes is None:
            continue
        name = name.upper()
        if name in seen:
            continue
        seen.add(name)
        fig["party_votes"].append({"party": name, "votes": votes})
        conf[f"party:{name}"] = _level(p.get("confidence"))
        if len(fig["party_votes"]) >= 40:
            break
    out["legible"] = bool(obj.get("legible", True)) and any(fig[k] is not None for k in COUNT_FIELDS)
    out["notes"] = _text(obj.get("notes"), 1000)
    if not out["legible"]:
        out["status"] = "unreadable"
    return out


def read_sheet(model: VisionModel | None, jpeg: bytes) -> dict[str, Any]:
    """Never raises for model trouble: the person can always type the figures in."""
    if model is None:
        return empty_reading("no_model", "No AI provider is set up, so type the figures in from the photo.")
    try:
        return parse_reading(model.read_image(PROMPT, jpeg))
    except LLMError as e:
        return empty_reading("failed", str(e))
