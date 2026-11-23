"""Collation check (Phase 10): declared ward / LGA / state totals vs the sum of their polling units. No AI.

Running a check: admin, analyst. Reading: every member. Each area whose declared total differs from its polling
units becomes a cross_check finding for review; the check itself is stored unchanged (append-only, audited).
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from app import idempotency
from app.auth import CurrentUser
from app.collation import engine as col
from app.contracts.models import CollationCheck, CollationList, CollationUpload
from app.deps import act_as, authorize, get_db, rate_limited_user, row_or_404

router = APIRouter()
RUNNERS = {"admin", "analyst"}
READERS = {"admin", "analyst", "researcher", "viewer", "coordinator"}
_COLS = """id, project_id, dataset_version_id, filename, sha256, summary, skipped, areas, finding_ids, created_at,
           created_by, org_id"""


def _view(r: dict[str, Any]) -> dict[str, Any]:
    return CollationCheck(
        id=r["id"], project_id=r["project_id"], dataset_version_id=r["dataset_version_id"], filename=r["filename"],
        sha256=r["sha256"], summary=r["summary"], skipped=r["skipped"], areas=r["areas"],
        finding_ids=[str(x) for x in r["finding_ids"] or []], created_at=r["created_at"], created_by=r["created_by"],
    ).model_dump(mode="json")  # fmt: skip


@router.post("/collation-checks", response_model=CollationCheck, status_code=201)
def create_check(
    body: CollationUpload,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    fingerprint = body.model_copy(update={"csv_text": hashlib.sha256(body.csv_text.encode()).hexdigest()})
    if (cached := idempotency.replay(db, user.id, idempotency_key, "POST /collation-checks", fingerprint)) is not None:
        return cached
    v = row_or_404(
        db.execute(
            """SELECT id, org_id, project_id, validated_at, archived_at FROM public.dataset_versions
               WHERE id = %s AND project_id = %s""",
            (str(body.dataset_version_id), str(body.project_id)),
        ).fetchone()
    )
    authorize(db, str(v["org_id"]), user, RUNNERS)
    if v["validated_at"] is None or v["archived_at"] is not None:
        raise HTTPException(status_code=409, detail="Choose a validated dataset version to compare the totals with.")
    raw = body.csv_text.encode("utf-8")
    try:
        declared = col.parse_declared(raw)
    except col.DeclaredError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    records = db.execute(
        """SELECT pu_code, state, lga, ward, registered_voters, accredited_voters, valid_votes, rejected_votes,
                  total_cast, party_votes FROM public.result_records WHERE dataset_version_id = %s""",
        (str(v["id"]),),
    ).fetchall()
    areas = col.compare(declared, col.aggregate(records))
    cid = str(uuid.uuid4())
    sha = hashlib.sha256(raw).hexdigest()
    org, pid, vid = str(v["org_id"]), str(v["project_id"]), str(v["id"])
    prov = {"source": "collation", "collation_check_id": cid, "file_sha256": sha,
            "engine_version": f"collation-check {col.ENGINE_VERSION}", "method_version": col.ENGINE_VERSION,
            "schema_version": "1.0.0", "ruleset_version": None}  # fmt: skip
    with db.transaction():
        act_as(db, user)
        finding_ids: list[str] = []
        for a in areas:
            a["finding_id"] = None
            if a["status"] != "mismatch":
                continue
            diffs = [x for x in a["figures"] if x["match"] is False]
            evidence = [{"collation_check_id": cid, "declared_row": a["row"], "units": a["units"],
                         "duplicate_units": a["duplicate_units"], "differences": diffs, "cells": []}]  # fmt: skip
            label = col.LEVEL_LABEL[a["level"]]
            fid = db.execute(
                """INSERT INTO public.findings (org_id, project_id, dataset_version_id, category, rule_id, state, lga,
                       ward, severity, confidence, title, explanation, limitations, evidence, provenance)
                   VALUES (%s,%s,%s,'cross_check',%s,%s,%s,%s,%s,'high',%s,%s,%s,%s,%s) RETURNING id::text""",
                (org, pid, vid, f"collation_{a['level']}", a["state"], a["lga"], a["ward"], col.severity(a),
                 f"Declared {label} total differs from the sum of its polling units ({col.area_name(a)})",
                 col.describe(a), col.LIMITATIONS, Jsonb(evidence), Jsonb(prov)),
            ).fetchone()["id"]  # fmt: skip
            a["finding_id"] = fid
            finding_ids.append(fid)
        summary = {"areas": len(areas), **{k: sum(1 for a in areas if a["status"] == k)
                                           for k in ("match", "mismatch", "incomplete", "unmatched")},
                   "rows_skipped": len(declared.skipped), "parties": declared.parties,
                   "ignored_columns": declared.ignored_columns}  # fmt: skip
        r = db.execute(
            f"""INSERT INTO public.collation_checks (id, org_id, project_id, dataset_version_id, filename, sha256,
                    engine_version, summary, skipped, areas, finding_ids, created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s) RETURNING {_COLS}""",  # noqa: S608
            (cid, org, pid, vid, body.filename.strip()[:255] or "declared-totals.csv", sha, col.ENGINE_VERSION,
             Jsonb(summary), Jsonb(declared.skipped), Jsonb(areas), finding_ids, user.id),
        ).fetchone()  # fmt: skip
    view = _view(r)
    idempotency.remember(db, user.id, idempotency_key, "POST /collation-checks", fingerprint, 201, view)
    return JSONResponse(status_code=201, content=view)


@router.get("/collation-checks", response_model=CollationList)
def list_checks(
    project_id: uuid.UUID,
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    p = row_or_404(db.execute("SELECT org_id FROM public.projects WHERE id = %s", (str(project_id),)).fetchone())
    authorize(db, str(p["org_id"]), user, READERS)
    rows = db.execute(
        f"SELECT {_COLS} FROM public.collation_checks WHERE project_id = %s ORDER BY created_at DESC LIMIT 100",  # noqa: S608
        (str(project_id),),
    ).fetchall()
    return CollationList(items=[_view(r) for r in rows]).model_dump(mode="json")


@router.get("/collation-checks/{id}", response_model=CollationCheck)
def get_check(
    id: uuid.UUID,
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    r = row_or_404(db.execute(f"SELECT {_COLS} FROM public.collation_checks WHERE id = %s", (str(id),)).fetchone())  # noqa: S608
    authorize(db, str(r["org_id"]), user, READERS)
    return _view(r)
