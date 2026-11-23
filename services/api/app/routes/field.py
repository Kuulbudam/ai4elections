"""Field reports (Phase 16): what an observer at a polling unit sees, sent from a phone in near real time.

Field agents report only from their assigned polling units and see only their own reports. Admins and coordinators
can report for any polling unit the organisation knows (from assignments or uploaded results). A resend with the same
client_id returns the report already stored. A critical incident is escalated to the area's coordinators at once.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from app.auth import CurrentUser
from app.contracts.models import FieldAssignmentList, FieldReport, FieldReportCreate, FieldReportList
from app.deps import act_as, authorize, get_db, rate_limited_user, row_or_404
from app.escalation import service as esc
from app.field import payloads

router = APIRouter()
REPORTERS = {"field_agent", "admin", "coordinator"}
READERS = {"admin", "analyst", "researcher", "viewer", "coordinator", "field_agent"}
_COLS = """id::text AS id, project_id::text AS project_id, client_id, kind, pu_code, state, lga, ward, payload,
           severity, sheet_id::text AS sheet_id, captured_at, received_at, lat, lng, accuracy_m,
           agent_id::text AS agent_id"""


def _code(v: str) -> str:
    return " ".join(str(v).split()).upper()


def _project(db: psycopg.Connection, project_id: str) -> dict[str, Any]:
    return row_or_404(db.execute("SELECT id::text AS id, org_id::text AS org_id FROM public.projects WHERE id = %s",
                                 (project_id,)).fetchone())  # fmt: skip


def _where(db: psycopg.Connection, org_id: str, project_id: str, pu: str, user_id: str, role: str) -> dict[str, str]:
    """State / LGA / ward of a polling unit the caller may report from."""
    sql = """SELECT state, lga, ward, pu_code FROM public.area_assignments
             WHERE org_id = %s AND level = 'polling_unit' AND upper(btrim(pu_code)) = %s"""
    args: list[Any] = [org_id, pu]
    if role == "field_agent":
        sql += " AND user_id = %s"
        args.append(user_id)
    a = db.execute(sql + " LIMIT 1", args).fetchone()
    if a:
        return a
    if role == "field_agent":
        raise HTTPException(status_code=403, detail="You can only report from the polling units you are assigned to.")
    r = db.execute(
        """SELECT r.state, r.lga, r.ward, r.pu_code FROM public.result_records r
           JOIN public.dataset_versions v ON v.id = r.dataset_version_id
           WHERE v.project_id = %s AND upper(btrim(r.pu_code)) = %s AND r.state IS NOT NULL LIMIT 1""",
        (project_id, pu),
    ).fetchone()
    if r:
        return r
    raise HTTPException(
        status_code=400, detail="That polling unit code isn't known in this organisation's areas or results."
    )


def _view(db: psycopg.Connection, r: dict[str, Any]) -> dict[str, Any]:
    name = db.execute("SELECT coalesce(display_name, email) AS n FROM public.profiles WHERE id = %s",
                      (r["agent_id"],)).fetchone()  # fmt: skip
    e = db.execute("SELECT id::text AS id FROM public.escalations WHERE source_kind = 'field_report' "
                   "AND source_id = %s ORDER BY created_at LIMIT 1", (r["id"],)).fetchone()  # fmt: skip
    loc = {"lat": r["lat"], "lng": r["lng"], "accuracy_m": r["accuracy_m"]} if r["lat"] is not None else None
    return FieldReport(
        id=r["id"], project_id=r["project_id"], client_id=r["client_id"], kind=r["kind"], pu_code=r["pu_code"],
        state=r["state"], lga=r["lga"], ward=r["ward"], payload=r["payload"], severity=r["severity"],
        sheet_id=r["sheet_id"], captured_at=r["captured_at"], received_at=r["received_at"], location=loc,
        agent_id=r["agent_id"], agent_name=name["n"] if name else None, escalation_id=e["id"] if e else None,
    ).model_dump(mode="json")  # fmt: skip


@router.get("/field/assignments", response_model=FieldAssignmentList)
def my_assignments(
    project_id: uuid.UUID, user: CurrentUser = Depends(rate_limited_user), db: psycopg.Connection = Depends(get_db)
) -> Any:
    p = _project(db, str(project_id))
    authorize(db, p["org_id"], user, REPORTERS)
    rows = db.execute(
        """SELECT pu_code, state, lga, ward FROM public.area_assignments
           WHERE org_id = %s AND user_id = %s AND level = 'polling_unit' ORDER BY pu_code""",
        (p["org_id"], user.id),
    ).fetchall()
    return {"items": rows}


@router.post("/field-reports", response_model=FieldReport, status_code=201)
def create_report(
    body: FieldReportCreate,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    p = _project(db, str(body.project_id))
    role = authorize(db, p["org_id"], user, REPORTERS)
    # A resend of a report already stored (the phone lost signal after sending) returns it unchanged.
    old = db.execute(f"SELECT {_COLS} FROM public.field_reports WHERE org_id = %s AND client_id = %s",  # noqa: S608
                     (p["org_id"], body.client_id)).fetchone()  # fmt: skip
    if old:
        if old["agent_id"] != user.id or old["project_id"] != p["id"]:
            raise HTTPException(status_code=409, detail="That report id was already used for a different report.")
        return JSONResponse(status_code=200, content=_view(db, old))
    kind = body.kind.value
    try:
        payload = payloads.parse(kind, body.payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"This {kind} report is incomplete: {e}") from e
    now = datetime.now(UTC)
    captured = body.captured_at if body.captured_at.tzinfo else body.captured_at.replace(tzinfo=UTC)
    if captured > now + timedelta(minutes=10) or captured < now - timedelta(days=7):
        raise HTTPException(
            status_code=400, detail="The phone's date or time looks wrong. Set it to automatic and resend."
        )
    where = _where(db, p["org_id"], p["id"], _code(body.pu_code), user.id, role)
    sheet_id = None
    if kind == "result":
        s = db.execute("SELECT id::text AS id, status, created_by::text AS created_by FROM public.result_sheets "
                       "WHERE id = %s AND project_id = %s", (payload["sheet_id"], p["id"])).fetchone()  # fmt: skip
        if s is None or (role == "field_agent" and s["created_by"] != user.id):
            raise HTTPException(status_code=404, detail="Not found")
        if s["status"] != "confirmed":
            raise HTTPException(status_code=409, detail="Check and confirm the sheet's figures first, then send.")
        sheet_id = s["id"]
    loc = body.location
    with db.transaction():
        act_as(db, user)
        r = db.execute(
            f"""INSERT INTO public.field_reports (org_id, project_id, client_id, agent_id, kind, state, lga, ward,
                    pu_code, payload, severity, sheet_id, captured_at, lat, lng, accuracy_m)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {_COLS}""",  # noqa: S608
            (p["org_id"], p["id"], body.client_id, user.id, kind, where["state"], where["lga"], where["ward"],
             where["pu_code"], Jsonb(payload), payload.get("severity") if kind == "incident" else None, sheet_id,
             captured, loc.lat if loc else None, loc.lng if loc else None, loc.accuracy_m if loc else None),
        ).fetchone()  # fmt: skip
        if kind == "incident" and payload["severity"] == "critical":
            esc.raise_escalation(
                db, org_id=p["org_id"], project_id=p["id"], source_kind="field_report", source_id=r["id"],
                title=f"{payloads.CATEGORY_LABEL[payload['category']]} at {where['pu_code']}", raised_by=user.id,
                reason=payload["description"], area=dict(where),
            )  # fmt: skip
    return JSONResponse(status_code=201, content=_view(db, r))


@router.get("/field-reports", response_model=FieldReportList)
def list_reports(
    project_id: uuid.UUID,
    since: datetime | None = None,
    kind: str | None = Query(None, pattern="^(arrival|accreditation|incident|result|closing)$"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    p = _project(db, str(project_id))
    role = authorize(db, p["org_id"], user, READERS)
    sql = f"SELECT {_COLS} FROM public.field_reports WHERE project_id = %s"  # noqa: S608 - fixed column list
    args: list[Any] = [p["id"]]
    if role == "field_agent":
        sql += " AND agent_id = %s"
        args.append(user.id)
    if since:
        sql += " AND received_at > %s"
        args.append(since)
    if kind:
        sql += " AND kind = %s"
        args.append(kind)
    rows = db.execute(sql + " ORDER BY received_at DESC LIMIT 500", args).fetchall()
    return {"items": [_view(db, r) for r in rows]}
