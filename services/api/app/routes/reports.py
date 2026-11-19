"""POST /reports and GET /reports/{id}/export (master prompt 5.9; ADR-0005, ADR-0011). No AI."""

from __future__ import annotations

import uuid
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from psycopg.types.json import Jsonb

from app import idempotency
from app.auth import CurrentUser
from app.contracts.models import Report, ReportRequest
from app.deps import act_as, authorize, get_db, rate_limited_user, row_or_404
from app.reports.build import build_body, content_hash, to_csv, to_markdown

router = APIRouter()
GENERATORS = {"admin", "analyst"}


def _view(r: dict[str, Any]) -> dict[str, Any]:
    return Report(
        id=r["id"], project_id=r["project_id"], analytical_run_id=r["analytical_run_id"],
        dataset_version_id=r["dataset_version_id"], title=r["title"], content_hash=r["content_hash"],
        audit_checkpoint={"audit_event_id": r["audit_event_id"], "event_hash": r["audit_event_hash"]},
        generated_at=r["generated_at"], generated_by=r["generated_by"],
    ).model_dump(mode="json")  # fmt: skip


@router.post("/reports", response_model=Report, status_code=201)
def create_report(
    body: ReportRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    if (cached := idempotency.replay(db, user.id, idempotency_key, "POST /reports", body)) is not None:
        return cached
    run = row_or_404(
        db.execute(
            """SELECT r.id, r.org_id, r.project_id, r.dataset_version_id, r.status, p.election_name,
                      v.original_filename, v.version_number
               FROM public.analytical_runs r JOIN public.projects p ON p.id = r.project_id
               JOIN public.dataset_versions v ON v.id = r.dataset_version_id
               WHERE r.id = %s AND r.project_id = %s""",
            (str(body.analytical_run_id), str(body.project_id)),
        ).fetchone()
    )
    authorize(db, str(run["org_id"]), user, GENERATORS)
    if run["status"] != "complete":
        raise HTTPException(status_code=409, detail="The analysis run is not complete")
    title = (body.title or "").strip() or (
        f"Findings report: {run['election_name']}, {run['original_filename']} v{run['version_number']}"
    )
    report_body = build_body(db, str(run["id"]))
    digest = content_hash(report_body)
    with db.transaction():
        act_as(db, user)
        # Lock the org's audit chain head so the checkpoint is the last event before this report.
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended('ai4e-audit:' || %s, 0))", (str(run["org_id"]),))
        cp = db.execute(
            "SELECT id, event_hash FROM public.audit_events WHERE org_id = %s ORDER BY seq DESC LIMIT 1",
            (str(run["org_id"]),),
        ).fetchone()
        r = db.execute(
            """INSERT INTO public.reports (org_id, project_id, analytical_run_id, dataset_version_id, title, body,
                   content_hash, audit_event_id, audit_event_hash, generated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id, project_id, analytical_run_id, dataset_version_id, title, content_hash, audit_event_id,
                         audit_event_hash, generated_at, generated_by""",
            (str(run["org_id"]), str(run["project_id"]), str(run["id"]), str(run["dataset_version_id"]), title[:200],
             Jsonb(report_body), digest, cp["id"] if cp else None, cp["event_hash"] if cp else None, user.id),
        ).fetchone()  # fmt: skip
    if r["audit_event_id"] is None:  # brand-new org with no events cannot happen (creating it is audited)
        raise HTTPException(status_code=500, detail="No audit checkpoint available")
    view = _view(r)
    idempotency.remember(db, user.id, idempotency_key, "POST /reports", body, 201, view)
    return JSONResponse(status_code=201, content=view)


@router.get("/reports/{report_id}/export")
def export_report(
    report_id: uuid.UUID,
    format: Literal["csv", "md"],  # noqa: A002 - query parameter name from the contract
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    r = row_or_404(
        db.execute(
            """SELECT r.*, coalesce(nullif(p.display_name, ''), p.email) AS generator
               FROM public.reports r LEFT JOIN public.profiles p ON p.id = r.generated_by WHERE r.id = %s""",
            (str(report_id),),
        ).fetchone()
    )
    authorize(db, str(r["org_id"]), user, GENERATORS)
    meta = {"title": r["title"], "content_hash": r["content_hash"], "generated_at": r["generated_at"].isoformat(),
            "generated_by": r["generator"], "audit_event_id": str(r["audit_event_id"]),
            "audit_event_hash": r["audit_event_hash"]}  # fmt: skip
    if format == "csv":
        text, media, ext = to_csv(meta, r["body"]), "text/csv; charset=utf-8", "csv"
    else:
        text, media, ext = to_markdown(meta, r["body"]), "text/markdown; charset=utf-8", "md"
    return Response(
        content=text.encode("utf-8"),
        media_type=media,
        headers={
            "X-Report-Content-Hash": r["content_hash"],
            "Content-Disposition": f'attachment; filename="ai4elections-report-{r["id"]}.{ext}"',
        },  # fmt: skip
    )
