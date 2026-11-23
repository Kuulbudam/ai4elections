"""Data assistant endpoints (founder requirement, 19 Nov; admins and analysts only)."""

from __future__ import annotations

import base64
import binascii
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from app import idempotency
from app.assistant import service, speech
from app.assistant.llm import ChatModel, LLMError, configured_model
from app.assistant.tools import Ctx
from app.auth import CurrentUser
from app.contracts.models import AssistantAnswer, AssistantQuestion, AssistantStatus, SpeechClip, Transcript
from app.deps import act_as, authorize, get_db, rate_limited_user, row_or_404

router = APIRouter()
ASKERS = {"admin", "analyst"}
SPEAKERS = {"admin", "analyst", "coordinator", "field_agent"}  # voice questions, and spoken notes in the field app


def get_model() -> ChatModel | None:
    return configured_model()


def get_transcriber() -> speech.Transcriber | None:
    return speech.configured()


@router.post("/assistant/transcribe", response_model=Transcript)
def transcribe(
    body: SpeechClip,
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
    stt: speech.Transcriber | None = Depends(get_transcriber),
) -> Any:
    """Turn a short recording into text. The audio is not stored; the person checks the text before it is used."""
    p = row_or_404(db.execute("SELECT org_id FROM public.projects WHERE id = %s", (str(body.project_id),)).fetchone())
    authorize(db, str(p["org_id"]), user, SPEAKERS)
    try:
        audio = base64.b64decode(body.audio_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(status_code=400, detail="The recording could not be read.") from e
    if not audio or len(audio) > speech.MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="Keep recordings under about three minutes.")
    if stt is None:
        raise HTTPException(status_code=503, detail="Voice is not set up yet (no AI provider key).")
    mime = body.mime_type.split(";")[0].strip().lower()
    language = getattr(body.language, "value", body.language) or "en"
    try:
        text = stt.transcribe(audio, mime, language)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not text:
        raise HTTPException(status_code=400, detail="Nothing was heard. Try again a little closer to the phone.")
    return {"text": text[:2000], "language": language, "model": stt.name}


@router.get("/assistant/status", response_model=AssistantStatus)
def status(_: CurrentUser = Depends(rate_limited_user), model: ChatModel | None = Depends(get_model)) -> Any:
    return AssistantStatus(enabled=model is not None, model=model.name if model else None).model_dump(mode="json")


@router.post("/assistant/ask", response_model=AssistantAnswer)
def ask(
    body: AssistantQuestion,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
    model: ChatModel | None = Depends(get_model),
) -> Any:
    if (cached := idempotency.replay(db, user.id, idempotency_key, "POST /assistant/ask", body)) is not None:
        return cached
    p = row_or_404(
        db.execute("SELECT id, org_id FROM public.projects WHERE id = %s", (str(body.project_id),)).fetchone()
    )
    authorize(db, str(p["org_id"]), user, ASKERS)
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Type a question.")
    if model is None:
        raise HTTPException(status_code=503, detail="The data assistant is not set up yet (no AI provider key).")
    ctx = Ctx(db=db, project_id=str(p["id"]), org_id=str(p["org_id"]))
    language = getattr(body.language, "value", body.language) or "en"
    history = [h.model_dump() for h in (body.history or [])]
    try:
        out = service.ask(model, ctx, question, history, language)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    with db.transaction():
        act_as(db, user)
        qid = db.execute(
            """INSERT INTO public.assistant_queries (org_id, project_id, user_id, question, answer, tools, citations,
                   grounded, model, language) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id::text""",
            (str(p["org_id"]), str(p["id"]), user.id, question[:2000], out["answer"], Jsonb(out["tool_calls"]),
             Jsonb(out["citations"]), out["grounded"], model.name, language),
        ).fetchone()["id"]  # fmt: skip
        db.execute(
            "SELECT app.log_event(%s, 'assistant.ask', 'assistant_queries', %s, %s)",
            (str(p["org_id"]), qid, Jsonb({"project_id": str(p["id"]), "grounded": out["grounded"],
                                           "lookups": [c["name"] for c in out["tool_calls"]]})),
        )  # fmt: skip
    view = AssistantAnswer(
        id=qid, answer=out["answer"], citations=out["citations"],
        lookups=[{"name": c["name"], "ok": c["ok"]} for c in out["tool_calls"]],
        grounded=out["grounded"], note=out["note"], model=model.name, language=language,
    ).model_dump(mode="json")  # fmt: skip
    idempotency.remember(db, user.id, idempotency_key, "POST /assistant/ask", body, 200, view)
    return JSONResponse(status_code=200, content=view)
