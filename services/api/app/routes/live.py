"""Live election-day view and signed submission packs (Phase 17).

GET  /projects/{id}/live               what observers have reported so far (counts only, no estimates)
GET  /projects/{id}/submission-packs   packs made for this election
POST /projects/{id}/submission-packs   make a signed pack (admins)
GET  /submission-packs/{id}/download   the zip, base64
POST /submission-packs/verify          check a pack: signature, key, file hashes, and whether we issued it
GET  /signing-key                      the public key (no sign-in needed, so anyone given a pack can check it)

Packs are handed over by the organisation itself. Nothing here sends data into any INEC system.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth import CurrentUser
from app.contracts.models import LiveSummary, PackCheck, PackVerify, SigningKey, SubmissionPack, SubmissionPackList
from app.deps import act_as, authorize, get_db, get_storage, rate_limited_user, row_or_404
from app.idempotency import remember, replay
from app.live import service as live
from app.packs import build as packs
from app.packs import signing
from app.storage import Storage

router = APIRouter()
READERS = {"admin", "analyst", "researcher", "viewer", "coordinator"}
MAKERS = {"admin"}
BUCKET = "packs"
_COLS = """p.id::text AS id, p.project_id::text AS project_id, p.created_at, p.manifest_sha256, p.zip_sha256,
           p.zip_bytes, p.key_id, p.counts, coalesce(pr.display_name, pr.email) AS created_by_name"""


def _project(db: psycopg.Connection, project_id: str) -> dict[str, Any]:
    return row_or_404(db.execute(
        """SELECT p.id::text AS id, p.org_id::text AS org_id, p.election_name, p.election_year, o.name AS org_name
           FROM public.projects p JOIN public.organisations o ON o.id = p.org_id WHERE p.id = %s""",
        (project_id,)).fetchone())  # fmt: skip


def _key() -> signing.Key:
    try:
        return signing.load()
    except signing.SigningNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/signing-key", response_model=SigningKey)
def signing_key() -> Any:
    k = _key()
    return {"algorithm": "Ed25519", "key_id": k.key_id, "public_key": k.public_b64}


@router.get("/projects/{project_id}/live", response_model=LiveSummary)
def live_summary(
    project_id: uuid.UUID, user: CurrentUser = Depends(rate_limited_user), db: psycopg.Connection = Depends(get_db)
) -> Any:
    p = _project(db, str(project_id))
    authorize(db, p["org_id"], user, READERS)
    return live.summary(db, p["org_id"], p["id"])


@router.get("/projects/{project_id}/submission-packs", response_model=SubmissionPackList)
def list_packs(
    project_id: uuid.UUID, user: CurrentUser = Depends(rate_limited_user), db: psycopg.Connection = Depends(get_db)
) -> Any:
    p = _project(db, str(project_id))
    authorize(db, p["org_id"], user, READERS)
    rows = db.execute(f"""SELECT {_COLS} FROM public.submission_packs p
                          LEFT JOIN public.profiles pr ON pr.id = p.created_by
                          WHERE p.project_id = %s ORDER BY p.created_at DESC LIMIT 50""",  # noqa: S608
                      (p["id"],)).fetchall()  # fmt: skip
    return {"items": rows}


@router.post("/projects/{project_id}/submission-packs", response_model=SubmissionPack, status_code=201)
def make_pack(
    project_id: uuid.UUID,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> Any:
    path_key = f"POST /projects/{project_id}/submission-packs"
    if (cached := replay(db, user.id, idempotency_key, path_key, None)) is not None:
        return cached
    p = _project(db, str(project_id))
    authorize(db, p["org_id"], user, MAKERS)
    key = _key()
    pack_id = str(uuid.uuid4())
    who = db.execute("SELECT coalesce(display_name, email) AS n FROM public.profiles WHERE id = %s",
                     (user.id,)).fetchone()  # fmt: skip
    made = packs.build(
        db, pack_id=pack_id, org={"id": p["org_id"], "name": p["org_name"]}, project=p,
        made_by=(who or {}).get("n") or user.id, key=key, api_url=str(request.base_url).rstrip("/"),
    )  # fmt: skip
    if len(made["zip"]) > packs.MAX_ZIP_BYTES:
        raise HTTPException(status_code=400, detail="This pack would be over 20 MB. Contact support.")
    zip_path = f"{p['org_id']}/{p['id']}/{pack_id}.zip"
    storage.put(zip_path, made["zip"], "application/zip", bucket=BUCKET)
    with db.transaction():
        act_as(db, user)
        db.execute(
            """INSERT INTO public.submission_packs (id, org_id, project_id, created_by, manifest_sha256, zip_sha256,
                   zip_path, zip_bytes, key_id, signature, counts)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (pack_id, p["org_id"], p["id"], user.id, made["manifest_sha256"], made["zip_sha256"], zip_path,
             len(made["zip"]), key.key_id, made["signature"], psycopg.types.json.Jsonb(made["counts"])),
        )  # fmt: skip
    row = db.execute(f"""SELECT {_COLS} FROM public.submission_packs p
                         LEFT JOIN public.profiles pr ON pr.id = p.created_by WHERE p.id = %s""",  # noqa: S608
                     (pack_id,)).fetchone()  # fmt: skip
    view = SubmissionPack.model_validate(row).model_dump(mode="json")
    remember(db, user.id, idempotency_key, path_key, None, 201, view)
    return JSONResponse(status_code=201, content=view)


@router.get("/submission-packs/{pack_id}/download")
def download_pack(
    pack_id: uuid.UUID,
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> Any:
    r = row_or_404(db.execute(
        """SELECT s.org_id::text AS org_id, s.zip_path, s.zip_sha256, s.created_at, p.election_name
           FROM public.submission_packs s JOIN public.projects p ON p.id = s.project_id WHERE s.id = %s""",
        (str(pack_id),)).fetchone())  # fmt: skip
    authorize(db, r["org_id"], user, READERS)
    data = storage.download(r["zip_path"], bucket=BUCKET)
    if data is None:
        raise HTTPException(status_code=404, detail="The pack file could not be found.")
    slug = "".join(c if c.isalnum() else "-" for c in r["election_name"].lower()).strip("-")[:40] or "election"
    return {"filename": f"eaas-pack-{slug}-{r['created_at']:%Y%m%d-%H%M}.zip", "zip_sha256": r["zip_sha256"],
            "zip_base64": base64.b64encode(data).decode()}  # fmt: skip


@router.post("/submission-packs/verify", response_model=PackCheck)
def verify_pack(
    body: PackVerify, user: CurrentUser = Depends(rate_limited_user), db: psycopg.Connection = Depends(get_db)
) -> Any:
    try:
        data = base64.b64decode(body.zip_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(status_code=400, detail="That file could not be read.") from e
    try:
        key: signing.Key | None = signing.load()
    except signing.SigningNotConfigured:
        key = None
    out = packs.check(data, key)
    m = out.pop("manifest") or {}
    issued = None
    if out["manifest_sha256"]:
        issued = db.execute("SELECT created_at FROM public.submission_packs WHERE manifest_sha256 = %s",
                            (out["manifest_sha256"],)).fetchone()  # fmt: skip
    out["checks"].append({"id": "issued", "ok": issued is not None, "title": "Made on this platform",
                          "detail": f"Recorded when it was made, {issued['created_at']:%d %b %Y %H:%M} UTC." if issued
                          else "There is no record of this pack being made here."})  # fmt: skip
    out["valid"] = out["valid"] and issued is not None
    return {**out, "pack_id": m.get("pack_id"), "generated_at": m.get("generated_at"),
            "organisation": (m.get("organisation") or {}).get("name"),
            "election": (m.get("election") or {}).get("name"),
            "key_id": m.get("key_id"), "counts": m.get("counts")}  # fmt: skip
