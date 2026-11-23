"""Review decisions and claims (openapi.yaml). Reads go through the Data API under RLS (ADR-0008).

Permissions follow the master prompt 5.1 table and are checked here AND in the database:
reviews by admin/analyst; claims by admin/analyst/researcher (always start unverified);
verification by an admin/analyst who did not create the claim, with evidence attached.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app import idempotency
from app.auth import CurrentUser
from app.contracts.models import (
    Claim,
    ClaimRequest,
    Confidence,
    Finding,
    FindingCategory,
    FindingPage,
    Indicator,
    Review,
    ReviewRequest,
    ReviewStatusFilter,
    Severity,
    VerifyClaimRequest,
)
from app.deps import act_as, authorize, get_db, rate_limited_user, row_or_404
from app.escalation import service as esc

router = APIRouter()

REVIEWERS = {"admin", "analyst"}
CONTRIBUTORS = {"admin", "analyst", "researcher"}
ALL_ROLES = {"admin", "analyst", "researcher", "viewer", "coordinator"}
SEV_RANK = {"very_high": 5, "high": 4, "medium": 3, "low": 2, "info": 1}

_SELECT = """
    SELECT f.id, f.org_id, f.project_id, f.dataset_version_id, f.analytical_run_id, f.category, f.indicator,
           f.rule_id, f.raw_row_id, f.pu_code, f.state, f.lga, f.ward, f.severity, f.confidence, f.title,
           f.explanation, f.limitations, f.peer_group, f.statistics, f.exclusions, f.evidence, f.provenance,
           f.created_at, o.review_status, o.note_count, o.claim_count, v.sha256 AS dataset_sha256,
           v.header -> 'columns' AS columns, w.cells, w.source_row_number,
           (CASE f.severity WHEN 'very_high' THEN 5 WHEN 'high' THEN 4 WHEN 'medium' THEN 3
                            WHEN 'low' THEN 2 ELSE 1 END) AS sev_rank
    FROM public.findings f
    JOIN public.finding_overview o ON o.id = f.id
    JOIN public.dataset_versions v ON v.id = f.dataset_version_id
    LEFT JOIN public.raw_rows w ON w.id = f.raw_row_id
"""


def _dec(x: Any, nd: int = 6) -> str | None:
    return None if x is None else f"{float(x):.{nd}f}"


def _finding_view(r: dict[str, Any]) -> dict[str, Any]:
    """Stored finding → contract Finding, with the source row's original cells as evidence."""
    cols: list[str] = list(r["columns"] or [])
    cells_list: list[str] = list(r["cells"] or [])
    cells = {c: (cells_list[i] if i < len(cells_list) else "") for i, c in enumerate(cols)}
    ev_stored = (r["evidence"] or [{}])[0]
    flagged = sorted({c["column"] for c in ev_stored.get("cells", []) if c.get("column")})
    evidence = []
    if r["raw_row_id"] is not None:
        evidence.append({"raw_row_id": r["raw_row_id"], "source_row_number": r["source_row_number"], "cells": cells,
                         **({"flagged_columns": flagged} if flagged else {})})  # fmt: skip
    pg = r["peer_group"]
    peer = None
    if pg:
        k = pg.get("key", {})
        peer = {"level": pg["level"], "key": "|".join(k.get(x, "") for x in ("state", "lga", "ward") if x in k),
                "sample_size": pg["n"], "fallback_used": pg["fallback_used"]}  # fmt: skip
    st = r["statistics"]
    stats = None
    if st:
        stats = {"value": _dec(st.get("value")), "median": _dec(st.get("median")), "mad": _dec(st.get("mad")),
                 "robust_z": _dec(st.get("robust_z"), 4), "threshold": _dec(st.get("threshold"), 1),
                 "direction": st.get("direction"), "leading_party": st.get("leading_party"),
                 "not_scored_reason": None}  # fmt: skip
    prov = r["provenance"] or {}
    return Finding(
        id=r["id"], project_id=r["project_id"], dataset_version_id=r["dataset_version_id"],
        analytical_run_id=r["analytical_run_id"], category=r["category"], indicator=r["indicator"],
        rule_id=r["rule_id"], pu_code=r["pu_code"], state=r["state"], lga=r["lga"], ward=r["ward"],
        severity=r["severity"], confidence=r["confidence"], title=r["title"], explanation=r["explanation"],
        limitations=r["limitations"], peer_group=peer, statistics=stats,
        exclusions=[{"raw_row_id": e["raw_row_id"], "pu_code": e.get("pu_code"), "reason": e.get("reason", ""),
                     **({"rules": e["rules"]} if e.get("rules") else {})} for e in (r["exclusions"] or [])],
        evidence=evidence, review_status=r["review_status"], note_count=r["note_count"],
        claim_count=r["claim_count"],
        provenance={"dataset_sha256": r["dataset_sha256"] or "0" * 64,
                    "schema_version": prov.get("schema_version") or "1.0.0",
                    "engine_version": prov.get("engine_version") or "unknown",
                    "method_version": prov.get("method_version"), "rule_version": prov.get("ruleset_version")},
        source_sheet_id=prov.get("sheet_id"), created_at=r["created_at"],
    ).model_dump(mode="json")  # fmt: skip


@router.get("/findings", response_model=FindingPage)
def list_findings(
    project_id: uuid.UUID,
    analytical_run_id: uuid.UUID | None = None,
    category: FindingCategory | None = None,
    indicator: Indicator | None = None,
    severity: Severity | None = None,
    confidence: Confidence | None = None,
    review_status: ReviewStatusFilter | None = None,
    state: str | None = Query(None, max_length=200),
    lga: str | None = Query(None, max_length=200),
    ward: str | None = Query(None, max_length=200),
    cursor: str | None = Query(None, max_length=512),
    limit: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    p = row_or_404(db.execute("SELECT id, org_id FROM public.projects WHERE id = %s", (str(project_id),)).fetchone())
    authorize(db, str(p["org_id"]), user, ALL_ROLES)
    filters = {"category": category, "indicator": indicator, "severity": severity, "confidence": confidence,
               "review_status": review_status, "state": state, "lga": lga, "ward": ward}  # fmt: skip
    rows, nxt = find_rows(db, str(p["id"]), analytical_run_id, filters, cursor, limit)
    return FindingPage(items=[_finding_view(r) for r in rows], next_cursor=nxt).model_dump(mode="json")


def scope(project_id: str, analytical_run_id: uuid.UUID | str | None = None) -> tuple[list[str], list[Any]]:
    """WHERE parts (and their values) for the findings people see for a project: what the Findings page lists."""
    where, args = ["project_id = %s"], [project_id]
    # Cross-checks (result sheets, collation) belong to a dataset version, not to an analysis run: they are listed
    # with the run of the version they were checked against.
    if analytical_run_id:
        where.append(
            "(analytical_run_id = %s OR (analytical_run_id IS NULL AND category = 'cross_check' AND dataset_version_id"
            " = (SELECT dataset_version_id FROM public.analytical_runs WHERE id = %s)))"
        )
        args += [str(analytical_run_id), str(analytical_run_id)]
    else:  # latest run of every version that is not archived, and cross-checks on any version not archived
        where.append(
            "(analytical_run_id IN (SELECT latest_analysis_run_id FROM public.dataset_versions "
            "WHERE project_id = %s AND archived_at IS NULL AND latest_analysis_run_id IS NOT NULL)"
            " OR (analytical_run_id IS NULL AND category = 'cross_check' AND dataset_version_id IN "
            "(SELECT id FROM public.dataset_versions WHERE project_id = %s AND archived_at IS NULL)))"
        )
        args += [project_id, project_id]
    return where, args


def find_rows(
    db: psycopg.Connection,
    project_id: str,
    analytical_run_id: uuid.UUID | str | None,
    filters: dict[str, Any],
    cursor: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    """Filtered, ordered page of findings for one project. Caller must have authorised the project."""
    where, args = scope(project_id, analytical_run_id)
    for col in ("category", "indicator", "severity", "confidence", "review_status"):
        val = filters.get(col)
        if val is not None:
            where.append(f"{col} = %s")
            args.append(getattr(val, "value", val))
    for col in ("state", "lga", "ward"):
        val = filters.get(col)
        if val and val.strip():
            where.append(f"lower({col}) = lower(%s)")
            args.append(" ".join(val.split()))
    if cursor:
        try:
            rank_s, last_id = cursor.split(":", 1)
            rank, last = int(rank_s), str(uuid.UUID(last_id))
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid cursor") from e
        where.append("(sev_rank < %s OR (sev_rank = %s AND id > %s))")
        args += [rank, rank, last]
    # Only fixed column names and placeholders are joined into the SQL; every value is a parameter.
    sql = f"SELECT * FROM ({_SELECT}) x WHERE {' AND '.join(where)} ORDER BY sev_rank DESC, id ASC LIMIT %s"  # noqa: S608
    rows = db.execute(sql, [*args, limit + 1]).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (f"{rows[-1]['sev_rank']}:{rows[-1]['id']}" if more and rows else None)


@router.get("/findings/{finding_id}", response_model=Finding)
def get_finding(
    finding_id: uuid.UUID,
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    r = row_or_404(db.execute(f"{_SELECT} WHERE f.id = %s", (str(finding_id),)).fetchone())
    authorize(db, str(r["org_id"]), user, ALL_ROLES)
    return _finding_view(r)


def _text(s: str, field: str, minimum: int) -> str:
    t = s.strip()
    if len(t) < minimum:
        raise HTTPException(status_code=400, detail=f"The {field} must be at least {minimum} characters.")
    return t


def _claim_view(db: psycopg.Connection, claim_id: str) -> dict[str, Any]:
    c = db.execute(
        """SELECT c.id, c.project_id, c.finding_id, c.statement, c.status, c.created_by, c.created_at,
                  c.verified_by, c.verified_at, c.verification_reason,
                  (SELECT count(*) FROM public.claim_evidence e WHERE e.claim_id = c.id)::int AS evidence_count
           FROM public.claims c WHERE c.id = %s""",
        (claim_id,),
    ).fetchone()
    return Claim(**c).model_dump(mode="json")


@router.post("/findings/{finding_id}/reviews", response_model=Review, status_code=201)
def create_review(
    finding_id: uuid.UUID,
    body: ReviewRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    if cached := idempotency.replay(db, user.id, idempotency_key, f"POST /findings/{finding_id}/reviews", body):
        return cached
    f = row_or_404(db.execute(
        "SELECT id, org_id, project_id, title, state, lga, ward, pu_code FROM public.findings WHERE id = %s",
        (str(finding_id),),
    ).fetchone())  # fmt: skip
    authorize(db, str(f["org_id"]), user, REVIEWERS)
    reason = _text(body.reason, "reason", 10)
    escalation_id = None
    with db.transaction():
        act_as(db, user)
        r = db.execute(
            """INSERT INTO public.finding_reviews (org_id, finding_id, decision, reason, reviewer_id)
               VALUES (%s,%s,%s,%s,%s) RETURNING id, finding_id, decision, reason, reviewer_id, created_at""",
            (str(f["org_id"]), str(f["id"]), body.decision.value, reason, user.id),
        ).fetchone()
        if body.decision.value == "escalated":  # "Escalate": route it up for someone to resolve
            escalation_id = esc.raise_escalation(
                db, org_id=str(f["org_id"]), project_id=str(f["project_id"]), source_kind="finding",
                source_id=str(f["id"]), title=f["title"], raised_by=user.id, reason=reason,
                area={k: f[k] for k in ("state", "lga", "ward", "pu_code")},
            )  # fmt: skip
    view = Review(**r, escalation_id=escalation_id).model_dump(mode="json")
    idempotency.remember(db, user.id, idempotency_key, f"POST /findings/{finding_id}/reviews", body, 201, view)
    return JSONResponse(status_code=201, content=view)


@router.post("/claims", response_model=Claim, status_code=201)
def create_claim(
    body: ClaimRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    if (cached := idempotency.replay(db, user.id, idempotency_key, "POST /claims", body)) is not None:
        return cached
    p = row_or_404(
        db.execute("SELECT id, org_id FROM public.projects WHERE id = %s", (str(body.project_id),)).fetchone()
    )
    org = str(p["org_id"])
    authorize(db, org, user, CONTRIBUTORS)
    if body.finding_id is not None:
        f = db.execute(
            "SELECT 1 FROM public.findings WHERE id = %s AND project_id = %s", (str(body.finding_id), str(p["id"]))
        ).fetchone()
        if f is None:
            raise HTTPException(status_code=404, detail="Finding not found in this project")
    statement = _text(body.statement, "statement", 10)
    evidence = body.evidence or []
    for e in evidence:
        if e.kind.value == "link" and e.url is None:
            raise HTTPException(status_code=400, detail="A link needs a URL.")
        if e.kind.value == "file" and e.file_sha256 is None:
            raise HTTPException(status_code=400, detail="A file needs its SHA-256.")
        if e.url is not None and not str(e.url).lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Links must start with http:// or https://.")
        _text(e.description, "evidence description", 1)
    with db.transaction():
        act_as(db, user)
        claim_id = db.execute(
            """INSERT INTO public.claims (org_id, project_id, finding_id, statement, created_by)
               VALUES (%s,%s,%s,%s,%s) RETURNING id::text""",
            (org, str(p["id"]), str(body.finding_id) if body.finding_id else None, statement, user.id),
        ).fetchone()["id"]
        for e in evidence:
            db.execute(
                """INSERT INTO public.claim_evidence (org_id, claim_id, kind, description, url, file_sha256, added_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (org, claim_id, e.kind.value, e.description.strip(), str(e.url) if e.url else None,
                 e.file_sha256, user.id),
            )  # fmt: skip
    view = _claim_view(db, claim_id)
    idempotency.remember(db, user.id, idempotency_key, "POST /claims", body, 201, view)
    return JSONResponse(status_code=201, content=jsonable_encoder(view))


@router.post("/claims/{claim_id}/verify", response_model=Claim)
def verify_claim(
    claim_id: uuid.UUID,
    body: VerifyClaimRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    endpoint = f"POST /claims/{claim_id}/verify"
    if (cached := idempotency.replay(db, user.id, idempotency_key, endpoint, body)) is not None:
        return cached
    c = row_or_404(
        db.execute(
            "SELECT id, org_id, status, created_by::text AS created_by FROM public.claims WHERE id = %s",
            (str(claim_id),),
        ).fetchone()
    )
    authorize(db, str(c["org_id"]), user, REVIEWERS)
    reason = _text(body.reason, "reason", 10)
    if c["status"] == "verified_fact":
        raise HTTPException(status_code=409, detail="This claim is already verified.")
    if c["created_by"] == user.id:
        raise HTTPException(status_code=409, detail="A claim must be verified by someone other than its author.")
    n = db.execute("SELECT count(*) AS n FROM public.claim_evidence WHERE claim_id = %s", (str(c["id"]),)).fetchone()
    if n["n"] == 0:
        raise HTTPException(status_code=409, detail="Attach at least one independent evidence item before verifying.")
    with db.transaction():
        act_as(db, user)
        db.execute(
            """UPDATE public.claims SET status = 'verified_fact', verified_by = %s, verified_at = now(),
                      verification_reason = %s
               WHERE id = %s AND status = 'unverified_allegation'""",
            (user.id, reason, str(c["id"])),
        )
    view = _claim_view(db, str(c["id"]))
    idempotency.remember(db, user.id, idempotency_key, endpoint, body, 200, view)
    return JSONResponse(status_code=200, content=jsonable_encoder(view))
