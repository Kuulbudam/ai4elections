"""Result-sheet reader (Phase 9): upload a photo of Form EC8A, AI reads it, a person confirms, the platform checks.

Uploading and confirming: admin, analyst, researcher (observers). Reading: every member.
The AI's reading is never used on its own: checks and findings are made only from figures a person confirmed.
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
from app.contracts.models import ResultSheet, SheetConfirm, SheetList, SheetUpload
from app.deps import act_as, authorize, get_db, get_storage, rate_limited_user, row_or_404
from app.sheets import checks as chk
from app.sheets.image import ImageRejected, clean, decode_base64
from app.sheets.read import VisionModel, configured_vision_model, empty_reading, read_sheet
from app.storage import Storage

router = APIRouter()
BUCKET = "sheets"
WRITERS = {"admin", "analyst", "researcher"}
READERS = {"admin", "analyst", "researcher", "viewer", "coordinator"}
CHECK_VERSION = "1.0.0"

LIMITATIONS = (
    "Based on one photo of one result sheet, read by AI and confirmed by a person. A mismatch can come from a "
    "misread or corrected figure, a data-entry error in the uploaded results, or a photo of a different polling "
    "unit. Look at the original sheet and the source data before drawing any conclusion."
)
TITLES = {
    "sheet_party_sum": "Result sheet: party scores do not add up to total valid votes",
    "sheet_total": "Result sheet: valid plus rejected votes do not equal total votes cast",
    "sheet_over_accredited": "Result sheet: votes cast exceed accredited voters",
    "sheet_over_registered": "Result sheet: accredited voters exceed registered voters",
    "sheet_vs_data": "Result sheet differs from the uploaded results",
}

_COLS = """id, org_id, project_id, dataset_version_id, status, model, extracted, confirmed, checks, comparison,
           image_path, image_sha256, finding_ids, created_at, created_by, confirmed_at, confirmed_by"""


def get_vision() -> VisionModel | None:
    return configured_vision_model()


def _view(r: dict[str, Any], image_url: str | None = None) -> dict[str, Any]:
    reading = r["extracted"] if (r["extracted"] or {}).get("status") else empty_reading("failed")
    return ResultSheet(
        id=r["id"], project_id=r["project_id"], dataset_version_id=r["dataset_version_id"], status=r["status"],
        model=r["model"], reading=reading, confirmed=r["confirmed"], checks=r["checks"],
        comparison=r["comparison"], finding_ids=[str(x) for x in (r["finding_ids"] or [])],
        image_sha256=r["image_sha256"], image_url=image_url, created_at=r["created_at"], created_by=r["created_by"],
        confirmed_at=r["confirmed_at"], confirmed_by=r["confirmed_by"],
    ).model_dump(mode="json")  # fmt: skip


@router.post("/sheets", response_model=ResultSheet, status_code=201)
def create_sheet(
    body: SheetUpload,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
    storage: Storage = Depends(get_storage),
    vision: VisionModel | None = Depends(get_vision),
) -> Any:
    fingerprint = body.model_copy(update={"image_base64": hashlib.sha256(body.image_base64.encode()).hexdigest()})
    if (cached := idempotency.replay(db, user.id, idempotency_key, "POST /sheets", fingerprint)) is not None:
        return cached
    v = row_or_404(
        db.execute(
            """SELECT v.id, v.org_id, v.project_id, v.validated_at, v.archived_at FROM public.dataset_versions v
               WHERE v.id = %s AND v.project_id = %s""",
            (str(body.dataset_version_id), str(body.project_id)),
        ).fetchone()
    )
    authorize(db, str(v["org_id"]), user, WRITERS)
    if v["validated_at"] is None or v["archived_at"] is not None:
        raise HTTPException(status_code=409, detail="Choose a validated dataset version to check the sheet against.")
    try:
        img = clean(decode_base64(body.image_base64))
    except ImageRejected as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    sid = str(uuid.uuid4())
    org, pid = str(v["org_id"]), str(v["project_id"])
    path = f"{org}/{pid}/{sid}.jpg"
    storage.put(path, img.jpeg, "image/jpeg", bucket=BUCKET)
    reading = read_sheet(vision, img.jpeg)
    with db.transaction():
        act_as(db, user)
        r = db.execute(
            f"""INSERT INTO public.result_sheets (id, org_id, project_id, dataset_version_id, image_path, image_sha256,
                    original_sha256, image_bytes, model, extracted, pu_code, created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {_COLS}""",  # noqa: S608 - fixed column list
            (sid, org, pid, str(v["id"]), path, img.sha256, img.original_sha256, len(img.jpeg),
             vision.name if vision and reading["status"] in ("read", "unreadable") else None, Jsonb(reading),
             reading["figures"].get("pu_code"), user.id),
        ).fetchone()  # fmt: skip
    view = _view(r, storage.signed_read_url(path, bucket=BUCKET))
    idempotency.remember(db, user.id, idempotency_key, "POST /sheets", fingerprint, 201, view)
    return JSONResponse(status_code=201, content=view)


@router.get("/sheets", response_model=SheetList)
def list_sheets(
    project_id: uuid.UUID,
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    p = row_or_404(db.execute("SELECT org_id FROM public.projects WHERE id = %s", (str(project_id),)).fetchone())
    authorize(db, str(p["org_id"]), user, READERS)
    rows = db.execute(
        f"SELECT {_COLS} FROM public.result_sheets WHERE project_id = %s ORDER BY created_at DESC LIMIT 200",  # noqa: S608
        (str(project_id),),
    ).fetchall()
    return SheetList(items=[_view(r) for r in rows]).model_dump(mode="json")


@router.get("/sheets/{id}", response_model=ResultSheet)
def get_sheet(
    id: uuid.UUID,
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> Any:
    r = row_or_404(db.execute(f"SELECT {_COLS} FROM public.result_sheets WHERE id = %s", (str(id),)).fetchone())  # noqa: S608
    authorize(db, str(r["org_id"]), user, READERS)
    return _view(r, storage.signed_read_url(r["image_path"], bucket=BUCKET))


def _clean_figures(fig: dict[str, Any]) -> dict[str, Any]:
    for k in ("pu_code", "state", "lga", "ward", "polling_unit_name"):
        val = fig.get(k)
        fig[k] = " ".join(str(val).split()) or None if val is not None else None
    seen: set[str] = set()
    parties = []
    for p in fig.get("party_votes") or []:
        name = " ".join(p["party"].split()).upper()
        if not name:
            continue
        if name in seen:
            raise HTTPException(status_code=400, detail=f"Party {name} is listed twice. Keep one row per party.")
        seen.add(name)
        parties.append({"party": name, "votes": p["votes"]})
    fig["party_votes"] = parties
    return fig


def _finding_rows(sheet: dict[str, Any], fig: dict[str, Any], checks: list[dict[str, Any]],
                  cmp: dict[str, Any], row: dict[str, Any] | None) -> list[tuple[Any, ...]]:  # fmt: skip
    where = row or {}
    code = (row or {}).get("pu_code") or fig.get("pu_code")
    prov = {"source": "result_sheet", "sheet_id": str(sheet["id"]), "image_sha256": sheet["image_sha256"],
            "engine_version": f"sheet-check {CHECK_VERSION}", "method_version": CHECK_VERSION,
            "schema_version": "1.0.0", "ruleset_version": None, "model": sheet["model"]}  # fmt: skip
    out = []
    for c in checks:
        if c["ok"] is not False:
            continue
        explanation = f"{c['detail']} The figures were read from a photo of the sheet and confirmed by a person."
        evidence = [{"sheet_id": str(sheet["id"]), "image_sha256": sheet["image_sha256"], "cells": [],
                     "comparison": [r for r in cmp.get("rows", []) if r["match"] is False]}]  # fmt: skip
        out.append((
            str(sheet["org_id"]), str(sheet["project_id"]), str(sheet["dataset_version_id"]), "cross_check",
            c["id"], (row or {}).get("raw_row_id"), code, where.get("state") or fig.get("state"),
            where.get("lga") or fig.get("lga"), where.get("ward") or fig.get("ward"), c["severity"] or "medium",
            "high" if c["id"] != "sheet_vs_data" else "medium", f"{TITLES[c['id']]} ({code or 'no code'})",
            explanation, LIMITATIONS, Jsonb(evidence), Jsonb(prov),
        ))  # fmt: skip
    return out


@router.post("/sheets/{id}/confirm", response_model=ResultSheet)
def confirm_sheet(
    id: uuid.UUID,
    body: SheetConfirm,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> Any:
    key_scope = f"POST /sheets/{id}/confirm"
    if (cached := idempotency.replay(db, user.id, idempotency_key, key_scope, body)) is not None:
        return cached
    sheet = row_or_404(db.execute(f"SELECT {_COLS} FROM public.result_sheets WHERE id = %s", (str(id),)).fetchone())  # noqa: S608
    authorize(db, str(sheet["org_id"]), user, WRITERS)
    if sheet["status"] == "confirmed":
        raise HTTPException(status_code=409, detail="This sheet has already been confirmed.")
    fig = _clean_figures(body.figures.model_dump(mode="json"))

    row = None
    code = chk.norm_code(fig.get("pu_code"))
    if code:
        row = db.execute(
            """SELECT r.raw_row_id::text AS raw_row_id, w.source_row_number, r.pu_code, r.state, r.lga, r.ward,
                      r.registered_voters, r.accredited_voters, r.valid_votes, r.rejected_votes, r.total_cast,
                      r.party_votes
               FROM public.result_records r JOIN public.raw_rows w ON w.id = r.raw_row_id
               WHERE r.dataset_version_id = %s
                 AND btrim(regexp_replace(upper(coalesce(r.pu_code, '')), '[^0-9A-Z]+', '/', 'g'), '/') = %s
               ORDER BY w.source_row_number LIMIT 1""",
            (str(sheet["dataset_version_id"]), code),
        ).fetchone()
    results = chk.arithmetic(fig)
    cmp = chk.compare(fig, row)
    results.append(chk.comparison_check(cmp))

    with db.transaction():
        act_as(db, user)
        locked = db.execute("SELECT status FROM public.result_sheets WHERE id = %s FOR UPDATE", (str(id),)).fetchone()
        if locked["status"] == "confirmed":
            raise HTTPException(status_code=409, detail="This sheet has already been confirmed.")
        finding_ids = []
        for values in _finding_rows(sheet, fig, results, cmp, row):
            finding_ids.append(db.execute(
                """INSERT INTO public.findings (org_id, project_id, dataset_version_id, category, rule_id, raw_row_id,
                       pu_code, state, lga, ward, severity, confidence, title, explanation, limitations, evidence,
                       provenance) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id::text""",
                values,
            ).fetchone()["id"])  # fmt: skip
        r = db.execute(
            f"""UPDATE public.result_sheets SET status = 'confirmed', confirmed = %s, checks = %s, comparison = %s,
                    pu_code = %s, raw_row_id = %s, finding_ids = %s::uuid[], confirmed_by = %s, confirmed_at = now()
                WHERE id = %s RETURNING {_COLS}""",  # noqa: S608
            (Jsonb(fig), Jsonb(results), Jsonb(cmp), fig.get("pu_code"), (row or {}).get("raw_row_id"),
             finding_ids, user.id, str(id)),
        ).fetchone()  # fmt: skip
    view = _view(r, storage.signed_read_url(r["image_path"], bucket=BUCKET))
    idempotency.remember(db, user.id, idempotency_key, key_scope, body, 200, view)
    return JSONResponse(status_code=200, content=view)
