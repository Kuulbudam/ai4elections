"""Escalations (Phase 14): list, read, resolve, send up. Raising happens where the decision is made (a review
decision "Escalate", or a critical incident from the field)."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app import idempotency
from app.auth import CurrentUser, member_role
from app.contracts.models import Escalation, EscalationList, EscalationNote
from app.deps import act_as, authorize, get_db, rate_limited_user, row_or_404
from app.escalation import service as esc

router = APIRouter()
READERS = {"admin", "analyst", "researcher", "viewer", "coordinator", "field_agent"}

_COLS = """id::text AS id, org_id::text AS org_id, project_id::text AS project_id, source_kind,
           source_id::text AS source_id, title, area, raised_by::text AS raised_by, reason, created_at, current_level,
           status, resolved_at, resolution"""


def _names(db: psycopg.Connection, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = db.execute(
        "SELECT id::text AS id, coalesce(display_name, email) AS name FROM public.profiles WHERE id = ANY(%s::uuid[])",
        (list(ids),),
    ).fetchall()
    return {r["id"]: r["name"] for r in rows}


def _view(db: psycopg.Connection, e: dict[str, Any], user_id: str, role: str | None, with_events: bool = True) -> dict:
    events = []
    if with_events:
        events = db.execute(
            """SELECT id::text AS id, kind, level, actor_id::text AS actor_id, note, created_at
               FROM public.escalation_events WHERE escalation_id = %s ORDER BY created_at""",
            (e["id"],),
        ).fetchall()
    names = _names(db, {e["raised_by"], *(x["actor_id"] for x in events)})
    can_resolve, can_send_up = esc.permissions(db, e, user_id, role)
    return Escalation(
        id=e["id"], project_id=e["project_id"], source_kind=e["source_kind"], source_id=e["source_id"],
        title=e["title"], area=e["area"] or {}, raised_by=e["raised_by"], raised_by_name=names.get(e["raised_by"]),
        reason=e["reason"], created_at=e["created_at"], current_level=e["current_level"], status=e["status"],
        resolved_at=e["resolved_at"], resolution=e["resolution"], can_resolve=can_resolve, can_send_up=can_send_up,
        events=[{**x, "actor_name": names.get(x["actor_id"])} for x in events],
    ).model_dump(mode="json")  # fmt: skip


def _load(db: psycopg.Connection, escalation_id: str) -> dict[str, Any]:
    sql = f"SELECT {_COLS} FROM public.escalation_overview WHERE id = %s"  # noqa: S608 - fixed column list
    return row_or_404(db.execute(sql, (escalation_id,)).fetchone())


@router.get("/escalations", response_model=EscalationList)
def list_escalations(
    project_id: uuid.UUID,
    status: str | None = Query(None, pattern="^(open|resolved)$"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    p = row_or_404(db.execute("SELECT org_id::text AS org_id FROM public.projects WHERE id = %s",
                              (str(project_id),)).fetchone())  # fmt: skip
    role = authorize(db, p["org_id"], user, READERS)
    q = f"SELECT {_COLS} FROM public.escalation_overview WHERE project_id = %s"  # noqa: S608 - fixed column list
    args: list[Any] = [str(project_id)]
    if status:
        q += " AND status = %s"
        args.append(status)
    rows = db.execute(q + " ORDER BY (status = 'open') DESC, updated_at DESC LIMIT 200", args).fetchall()
    return {"items": [_view(db, r, user.id, role, with_events=False) for r in rows]}


@router.get("/escalations/{escalation_id}", response_model=Escalation)
def get_escalation(
    escalation_id: uuid.UUID, user: CurrentUser = Depends(rate_limited_user), db: psycopg.Connection = Depends(get_db)
) -> Any:
    e = _load(db, str(escalation_id))
    role = authorize(db, e["org_id"], user, READERS)
    return _view(db, e, user.id, role)


def _act(kind: str, escalation_id: uuid.UUID, body: EscalationNote, key: str | None, user: CurrentUser,
         db: psycopg.Connection) -> Any:  # fmt: skip
    path = f"POST /escalations/{escalation_id}/{kind}"
    if (cached := idempotency.replay(db, user.id, key, path, body)) is not None:
        return cached
    e = _load(db, str(escalation_id))
    role = member_role(db, e["org_id"], user.id)
    if role is None:
        raise HTTPException(status_code=404, detail="Not found")
    note = " ".join(body.note.split())
    if len(note) < 5:
        raise HTTPException(status_code=400, detail="Write a short note (at least 5 characters).")
    with db.transaction():
        act_as(db, user)
        if kind == "resolve":
            esc.resolve(db, str(escalation_id), user.id, role, note)
        else:
            esc.send_up(db, str(escalation_id), user.id, role, note)
    view = _view(db, _load(db, str(escalation_id)), user.id, role)
    idempotency.remember(db, user.id, key, path, body, 200, view)
    return JSONResponse(status_code=200, content=view)


@router.post("/escalations/{escalation_id}/resolve", response_model=Escalation)
def resolve_escalation(
    escalation_id: uuid.UUID,
    body: EscalationNote,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    return _act("resolve", escalation_id, body, idempotency_key, user, db)


@router.post("/escalations/{escalation_id}/send-up", response_model=Escalation)
def send_up_escalation(
    escalation_id: uuid.UUID,
    body: EscalationNote,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    return _act("send-up", escalation_id, body, idempotency_key, user, db)
