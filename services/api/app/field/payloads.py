"""What each kind of field report must contain. Strict: unknown fields are refused, counts are whole and sensible."""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_COUNT = 100_000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Arrival(_Strict):
    opened: bool
    opened_at: str | None = Field(None, description="HH:MM local time")
    officials_present: int = Field(ge=0, le=50)
    bvas_working: bool
    materials_complete: bool
    security_present: bool

    @field_validator("opened_at")
    @classmethod
    def _time(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("opened_at must look like 08:30")
        return v


class Accreditation(_Strict):
    accredited_on_bvas: int = Field(ge=0, le=MAX_COUNT, description="the count the BVAS shows")
    bvas_working: bool
    voters_waiting: int | None = Field(None, ge=0, le=MAX_COUNT)


class Incident(_Strict):
    category: Literal["bvas_failure", "late_opening", "materials_missing", "violence", "intimidation", "vote_buying",
                      "ballot_snatching", "agents_denied", "observers_denied", "other"]  # fmt: skip
    severity: Literal["low", "medium", "critical"]
    description: str = Field(min_length=5, max_length=2000)
    ongoing: bool

    @field_validator("description")
    @classmethod
    def _tidy(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 5:
            raise ValueError("describe what happened in a few words")
        return v


class Result(_Strict):
    sheet_id: uuid.UUID


class Closing(_Strict):
    counting_done: bool
    result_posted_publicly: bool
    bvas_upload_seen: bool
    agents_signed: bool


MODELS: dict[str, type[_Strict]] = {"arrival": Arrival, "accreditation": Accreditation, "incident": Incident,
                                    "result": Result, "closing": Closing}  # fmt: skip

CATEGORY_LABEL = {
    "bvas_failure": "BVAS not working", "late_opening": "Polls opened late", "materials_missing": "Materials missing",
    "violence": "Violence", "intimidation": "Intimidation of voters", "vote_buying": "Vote buying",
    "ballot_snatching": "Ballot box snatched", "agents_denied": "Party agents denied entry",
    "observers_denied": "Observers denied entry", "other": "Other incident",
}  # fmt: skip


def parse(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validated payload as plain JSON, or ValueError with a short reason."""
    try:
        return MODELS[kind].model_validate(payload).model_dump(mode="json")
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(x) for x in first.get("loc", ())) or "payload"
        raise ValueError(f"{where}: {first.get('msg', 'invalid')}") from None
