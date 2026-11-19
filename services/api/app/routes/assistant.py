"""Data assistant endpoints (founder requirement, 19 Nov; admins and analysts only)."""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from app import idempotency
from app.assistant import service
from app.assistant.llm import ChatModel, LLMError, configured_model
from app.assistant.tools import Ctx
from app.auth import CurrentUser
from app.contracts.models import AssistantAnswer, AssistantQuestion, AssistantStatus
from app.deps import act_as, authorize, get_db, rate_limited_user, row_or_404

router = APIRouter()
ASKERS = {"admin", "analyst"}


def get_model() -> ChatModel | None:
    return configured_model()


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
    history = [h.model_dump() for h in (body.history or [])]
    try:
        out = service.ask(model, ctx, question, history)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    with db.transaction():
        act_as(db, user)
        qid = db.execute(
            """INSERT INTO public.assistant_queries (org_id, project_id, user_id, question, answer, tools, citations,
                   grounded, model) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id::text""",
            (str(p["org_id"]), str(p["id"]), user.id, question[:2000], out["answer"], Jsonb(out["tool_calls"]),
             Jsonb(out["citations"]), out["grounded"], model.name),
        ).fetchone()["id"]  # fmt: skip
        db.execute(
            "SELECT app.log_event(%s, 'assistant.ask', 'assistant_queries', %s, %s)",
            (str(p["org_id"]), qid, Jsonb({"project_id": str(p["id"]), "grounded": out["grounded"],
                                           "lookups": [c["name"] for c in out["tool_calls"]]})),
        )  # fmt: skip
    view = AssistantAnswer(
        id=qid, answer=out["answer"], citations=out["citations"],
        lookups=[{"name": c["name"], "ok": c["ok"]} for c in out["tool_calls"]],
        grounded=out["grounded"], note=out["note"], model=model.name,
    ).model_dump(mode="json")  # fmt: skip
    idempotency.remember(db, user.id, idempotency_key, "POST /assistant/ask", body, 200, view)
    return JSONResponse(status_code=200, content=view)
