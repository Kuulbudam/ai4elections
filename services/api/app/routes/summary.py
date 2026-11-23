"""AI summary at the end of Findings (Phase 19).

GET  /projects/{id}/findings-summary?language=   the latest summary in that language, and whether the counts changed
POST /projects/{id}/findings-summary             write a new one from the current counts
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from app import findings_summary as fs
from app import idempotency
from app.assistant.llm import ChatModel, LLMError, configured_model
from app.auth import CurrentUser
from app.contracts.models import FindingsSummary, FindingsSummaryRequest
from app.deps import act_as, authorize, get_db, rate_limited_user, row_or_404

router = APIRouter()
READERS = {"admin", "analyst", "researcher", "viewer", "coordinator"}
WRITERS = {"admin", "analyst", "researcher", "coordinator"}  # viewers read the latest one; writing uses AI quota
_FROM = "FROM public.findings_summaries s LEFT JOIN public.profiles p ON p.id = s.created_by"
_COLS = """s.id::text AS id, s.project_id::text AS project_id, s.created_at, s.language, s.facts, s.facts_sha256,
           s.summary, s.grounded, s.note, s.model, coalesce(p.display_name, p.email) AS created_by_name"""


def get_model() -> ChatModel | None:
    return configured_model()


def _project(db: psycopg.Connection, project_id: str) -> dict[str, Any]:
    return row_or_404(db.execute("SELECT id::text AS id, org_id::text AS org_id FROM public.projects WHERE id = %s",
                                 (project_id,)).fetchone())  # fmt: skip


def _view(row: dict[str, Any], current_sha: str) -> dict[str, Any]:
    data = {k: v for k, v in row.items() if k != "facts_sha256"}
    return FindingsSummary.model_validate({**data, "stale": row["facts_sha256"] != current_sha}).model_dump(mode="json")


@router.get("/projects/{project_id}/findings-summary", response_model=FindingsSummary)
def latest(
    project_id: uuid.UUID,
    language: str = Query("en", pattern="^(en|ha|yo|ig|pcm)$"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    p = _project(db, str(project_id))
    authorize(db, p["org_id"], user, READERS)
    row = db.execute(f"""SELECT {_COLS} {_FROM}
                         WHERE s.project_id = %s AND s.language = %s ORDER BY s.created_at DESC LIMIT 1""",  # noqa: S608
                     (p["id"], language)).fetchone()  # fmt: skip
    if row is None:
        raise HTTPException(status_code=404, detail="No summary has been written yet.")
    return _view(row, fs.fingerprint(fs.facts(db, p["id"])))


@router.post("/projects/{project_id}/findings-summary", response_model=FindingsSummary, status_code=201)
def write(
    project_id: uuid.UUID,
    body: FindingsSummaryRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
    model: ChatModel | None = Depends(get_model),
) -> Any:
    path = f"POST /projects/{project_id}/findings-summary"
    if (cached := idempotency.replay(db, user.id, idempotency_key, path, body)) is not None:
        return cached
    p = _project(db, str(project_id))
    authorize(db, p["org_id"], user, WRITERS)
    language = getattr(body.language, "value", body.language) or "en"
    facts = fs.facts(db, p["id"])
    sha = fs.fingerprint(facts)
    if model is None and facts["findings"]:
        raise HTTPException(status_code=503, detail="The AI summary is not set up yet (no AI provider key).")
    try:
        out = fs.write(model, facts, language)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    with db.transaction():
        act_as(db, user)
        sid = db.execute(
            """INSERT INTO public.findings_summaries (org_id, project_id, created_by, language, facts, facts_sha256,
                   summary, grounded, note, model) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id::text""",
            (p["org_id"], p["id"], user.id, language, Jsonb(facts), sha, out["text"][:4000], out["grounded"],
             out["note"], model.name if model and out["note"] != "empty" else None),
        ).fetchone()["id"]  # fmt: skip
    row = db.execute(f"SELECT {_COLS} {_FROM} WHERE s.id = %s", (sid,)).fetchone()  # noqa: S608
    view = _view(row, sha)
    idempotency.remember(db, user.id, idempotency_key, path, body, 201, view)
    return JSONResponse(status_code=201, content=view)
