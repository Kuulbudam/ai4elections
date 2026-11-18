"""Upload, finalize, mapping and job-status endpoints (openapi.yaml)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from app import idempotency
from app.auth import CurrentUser
from app.contracts.models import (
    ColumnMapping,
    ColumnMappingSaved,
    FinalizeResponse,
    Job,
    JobRequest,
    MappingIssue,
    UploadRequest,
    UploadResponse,
)
from app.deps import (
    Dispatcher,
    act_as,
    authorize,
    get_db,
    get_dispatcher,
    get_storage,
    rate_limited_user,
    row_or_404,
)
from app.ingest.csv_parse import ParseError, check_upload_prefix
from app.jobs.queue import enqueue_job, job_view
from app.storage import Storage
from app.validation.engine import check_mapping
from app.validation.rules import RULESET_VERSION

log = logging.getLogger("ai4e.api")

router = APIRouter()

UPLOADERS = {"admin", "analyst"}
ALL_ROLES = {"admin", "analyst", "researcher", "viewer"}
REQUIRED_FIELDS = [
    "pu_code", "state", "lga", "ward", "registered_voters", "accredited_voters", "valid_votes",
]  # fmt: skip
OPTIONAL_FIELDS = ["rejected_votes", "total_cast", "latitude", "longitude"]
MAX_BYTES = 25 * 1024 * 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _clean_filename(name: str) -> str:
    name = _CONTROL.sub("", name).replace("/", "_").replace("\\", "_").strip()
    return name[:255] or "upload.csv"


# --------------------------------------------------------------------------- POST /uploads


@router.post("/uploads", status_code=201, response_model=UploadResponse)
def create_upload(
    body: UploadRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> Any:
    if (cached := idempotency.replay(db, user.id, idempotency_key, "POST /uploads", body)) is not None:
        return cached
    filename = _clean_filename(body.original_filename)
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files can be uploaded")

    project = row_or_404(
        db.execute(
            "SELECT id, org_id, archived_at FROM public.projects WHERE id = %s", (str(body.project_id),)
        ).fetchone()
    )
    org_id, project_id = str(project["org_id"]), str(project["id"])
    authorize(db, org_id, user, UPLOADERS)
    if project["archived_at"] is not None:
        raise HTTPException(status_code=409, detail="This project is archived")

    with db.transaction():
        act_as(db, user)
        if body.dataset_id is not None:
            dataset = row_or_404(
                db.execute(
                    "SELECT id FROM public.datasets WHERE id = %s AND org_id = %s AND project_id = %s",
                    (str(body.dataset_id), org_id, project_id),
                ).fetchone()
            )
            dataset_id = str(dataset["id"])
        else:
            name = (body.dataset_name or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="dataset_name is required when creating a new dataset")
            dataset_id = str(
                db.execute(
                    "INSERT INTO public.datasets (org_id, project_id, name, created_by) "
                    "VALUES (%s,%s,%s,%s) RETURNING id",
                    (org_id, project_id, name[:200], user.id),
                ).fetchone()["id"]
            )
        source_id = db.execute(
            """INSERT INTO public.data_sources (org_id, project_id, source_type, description, created_by)
               VALUES (%s,%s,%s,%s,%s) RETURNING id""",
            (org_id, project_id, body.source_type.value, body.source_description.strip(), user.id),
        ).fetchone()["id"]
        # Serialise version numbering per dataset.
        db.execute("SELECT 1 FROM public.datasets WHERE id = %s FOR UPDATE", (dataset_id,))
        version_number = db.execute(
            "SELECT coalesce(max(version_number), 0) + 1 AS n FROM public.dataset_versions WHERE dataset_id = %s",
            (dataset_id,),
        ).fetchone()["n"]
        version_id = str(uuid.uuid4())
        path = f"{org_id}/{project_id}/{dataset_id}/{version_id}.csv"  # server-generated; no user input
        db.execute(
            """INSERT INTO public.dataset_versions (id, org_id, project_id, dataset_id, data_source_id, version_number,
                   storage_path, original_filename, content_type, declared_size_bytes, uploaded_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (version_id, org_id, project_id, dataset_id, source_id, version_number, path, filename,
             body.content_type.value, body.size_bytes, user.id),
        )  # fmt: skip
        signed = storage.create_signed_upload(path)
        response = {
            "dataset_id": dataset_id,
            "dataset_version_id": version_id,
            "version_number": version_number,
            "upload_url": signed.url,
            "upload_token": signed.token,
            "expires_at": signed.expires_at,
        }
        idempotency.remember(db, user.id, idempotency_key, "POST /uploads", body, 201, response)
    return JSONResponse(status_code=201, content=jsonable_encoder(UploadResponse(**response)))


# --------------------------------------------------------------------------- POST /dataset-versions/{id}/finalize


def _finalize_view(v: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    return FinalizeResponse(
        dataset_version_id=v["id"],
        status=v["status"],
        sha256=v["sha256"],
        size_bytes=v["size_bytes"],
        duplicate_of_version_id=v["duplicate_of_version_id"],
        ingestion_job=Job(**job_view(job)),
    ).model_dump(mode="json")


@router.post("/dataset-versions/{version_id}/finalize", response_model=FinalizeResponse)
def finalize_version(
    version_id: uuid.UUID,
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
    storage: Storage = Depends(get_storage),
    dispatch: Dispatcher = Depends(get_dispatcher),
) -> Any:
    v = row_or_404(db.execute("SELECT * FROM public.dataset_versions WHERE id = %s", (str(version_id),)).fetchone())
    authorize(db, str(v["org_id"]), user, UPLOADERS)

    if v["finalized_at"] is not None:  # one-time operation: repeat returns the same result
        job = db.execute(
            "SELECT * FROM public.processing_jobs WHERE target_id = %s AND job_type = 'ingest' "
            "ORDER BY created_at DESC LIMIT 1",
            (str(v["id"]),),
        ).fetchone()
        return _finalize_view(v, job)

    obj = storage.inspect(v["storage_path"])
    if obj is None:
        raise HTTPException(status_code=409, detail="No uploaded file found for this version. Upload the file first.")
    if obj.size > MAX_BYTES:
        raise HTTPException(status_code=400, detail="The file is larger than 25 MB")
    try:
        check_upload_prefix(obj.head)
    except ParseError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    with db.transaction():
        act_as(db, user)
        locked = db.execute(
            "SELECT finalized_at FROM public.dataset_versions WHERE id = %s FOR UPDATE", (str(v["id"]),)
        ).fetchone()
        if locked["finalized_at"] is not None:
            raise HTTPException(status_code=409, detail="This version was finalized by another request")
        dup = db.execute(
            """SELECT id FROM public.dataset_versions
               WHERE dataset_id = %s AND sha256 = %s AND id <> %s ORDER BY version_number LIMIT 1""",
            (str(v["dataset_id"]), obj.sha256, str(v["id"])),
        ).fetchone()
        v = db.execute(
            """UPDATE public.dataset_versions
               SET size_bytes = %s, sha256 = %s, finalized_at = now(), status = 'uploaded', duplicate_of_version_id = %s
               WHERE id = %s RETURNING *""",
            (obj.size, obj.sha256, dup["id"] if dup else None, str(v["id"])),
        ).fetchone()
        job, _ = enqueue_job(
            db,
            org_id=str(v["org_id"]),
            project_id=str(v["project_id"]),
            job_type="ingest",
            target_id=str(v["id"]),
            input_fingerprint=obj.sha256,
            created_by=user.id,
        )
    record_dispatch(db, str(job["id"]), dispatch(str(job["id"])))
    return _finalize_view(v, job)


def record_dispatch(db: psycopg.Connection, job_id: str, result: str) -> None:
    """Best effort: a bookkeeping failure must never fail the user's request."""
    try:
        db.execute(
            "UPDATE public.processing_jobs SET dispatch_result = %s, dispatched_at = now() WHERE id = %s",
            (result, job_id),
        )
    except psycopg.Error:
        log.warning("could not record dispatch result for job %s", job_id)


# --------------------------------------------------------------------------- PUT /dataset-versions/{id}/mapping


def mapping_issues(mapping: ColumnMapping, columns: list[str]) -> list[MappingIssue]:
    issues: list[MappingIssue] = []
    known = set(columns)
    fields: dict[str, str] = {str(k.value if hasattr(k, "value") else k): v for k, v in mapping.fields.items()}
    for f in REQUIRED_FIELDS:
        if f not in fields:
            issues.append(MappingIssue(rule_id="V001", field=f, message=f"Required field '{f}' is not mapped."))
    if ("latitude" in fields) != ("longitude" in fields):
        issues.append(
            MappingIssue(rule_id="V001", field="latitude", message="Latitude and longitude must be mapped together.")
        )
    used: dict[str, list[str]] = {}
    for f, col in fields.items():
        used.setdefault(col, []).append(f)
    for p in mapping.party_columns:
        used.setdefault(p.source_column, []).append(f"party:{p.party_code}")
    for col, targets in used.items():
        if col not in known:
            issues.append(
                MappingIssue(rule_id="V001", field=targets[0], message=f"Column '{col}' does not exist in this file.")
            )
        if len(targets) > 1:
            issues.append(
                MappingIssue(
                    rule_id="V002",
                    field=targets[0],
                    message=f"Column '{col}' is assigned to {', '.join(targets)}.",
                )
            )
    codes = [p.party_code for p in mapping.party_columns]
    for code in sorted({c for c in codes if codes.count(c) > 1}):
        issues.append(MappingIssue(rule_id="V002", field=f"party:{code}", message=f"Party code {code} is used twice."))
    return issues


@router.put("/dataset-versions/{version_id}/mapping", response_model=ColumnMappingSaved)
def save_mapping(
    version_id: uuid.UUID,
    body: ColumnMapping,
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    v = row_or_404(
        db.execute(
            "SELECT id, org_id, status, header, archived_at FROM public.dataset_versions WHERE id = %s",
            (str(version_id),),
        ).fetchone()
    )
    org_id = str(v["org_id"])
    authorize(db, org_id, user, UPLOADERS)
    if v["status"] != "ingested" or not v["header"]:
        raise HTTPException(status_code=409, detail="The file must finish ingesting before columns can be mapped")
    if v["archived_at"] is not None:
        raise HTTPException(status_code=409, detail="This version is archived")
    issues = mapping_issues(body, list(v["header"]["columns"]))
    stored = jsonable_encoder(body.model_dump(exclude={"save_as_template_name"}))
    template_id = None
    with db.transaction():
        act_as(db, user)
        db.execute(
            "UPDATE public.dataset_versions SET mapping = %s, mapping_saved_at = now() WHERE id = %s",
            (Jsonb(stored), str(v["id"])),
        )
        if body.save_as_template_name and body.save_as_template_name.strip():
            template_id = db.execute(
                """INSERT INTO public.mapping_templates (org_id, name, mapping, created_by) VALUES (%s,%s,%s,%s)
                   ON CONFLICT (org_id, name) DO UPDATE SET mapping = EXCLUDED.mapping RETURNING id""",
                (org_id, body.save_as_template_name.strip()[:120], Jsonb(stored), user.id),
            ).fetchone()["id"]
    return ColumnMappingSaved(
        dataset_version_id=v["id"], schema_version=body.schema_version, template_id=template_id, issues=issues
    ).model_dump(mode="json")


# --------------------------------------------------------------------------- POST /jobs

RUNNERS = {"admin", "analyst", "researcher"}


@router.post("/jobs", response_model=Job, status_code=202)
def create_job(
    body: JobRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
    dispatch: Dispatcher = Depends(get_dispatcher),
) -> Any:
    if (cached := idempotency.replay(db, user.id, idempotency_key, "POST /jobs", body)) is not None:
        return cached
    v = row_or_404(
        db.execute(
            "SELECT id, org_id, project_id, status, header, mapping, archived_at FROM public.dataset_versions "
            "WHERE id = %s",
            (str(body.dataset_version_id),),
        ).fetchone()
    )
    authorize(db, str(v["org_id"]), user, RUNNERS)
    job_type = body.job_type.value
    if job_type != "validate":
        raise HTTPException(status_code=400, detail="Analysis jobs are not available yet")
    if v["archived_at"] is not None:
        raise HTTPException(status_code=409, detail="This version is archived")
    if v["status"] != "ingested":
        raise HTTPException(status_code=409, detail="The file must finish ingesting first")
    if not v["mapping"]:
        raise HTTPException(status_code=409, detail="Map the columns before running validation")
    if check_mapping(list(v["header"]["columns"]), v["mapping"]):
        raise HTTPException(status_code=409, detail="The column mapping is incomplete. Fix it before validating.")
    units = db.execute(
        "SELECT state, lga, ward FROM public.admin_units WHERE project_id = %s ORDER BY state, lga, ward",
        (str(v["project_id"]),),
    ).fetchall()
    fingerprint = hashlib.sha256(
        json.dumps(
            {"mapping": v["mapping"], "ruleset": RULESET_VERSION, "units": [list(u.values()) for u in units]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    with db.transaction():
        act_as(db, user)
        job, created = enqueue_job(
            db,
            org_id=str(v["org_id"]),
            project_id=str(v["project_id"]),
            job_type=job_type,
            target_id=str(v["id"]),
            input_fingerprint=fingerprint,
            created_by=user.id,
            payload={"mapping": v["mapping"], "ruleset_version": RULESET_VERSION},
        )
    if created:
        record_dispatch(db, str(job["id"]), dispatch(str(job["id"])))
    view = Job(**job_view(job)).model_dump(mode="json")
    status = 202 if created else 200
    idempotency.remember(db, user.id, idempotency_key, "POST /jobs", body, status, view)
    return JSONResponse(status_code=status, content=view)


# --------------------------------------------------------------------------- GET /jobs/{id}


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    job = row_or_404(db.execute("SELECT * FROM public.processing_jobs WHERE id = %s", (str(job_id),)).fetchone())
    authorize(db, str(job["org_id"]), user, ALL_ROLES)
    run = db.execute(
        "SELECT id FROM public.analytical_runs WHERE job_id = %s AND status = 'complete'", (str(job["id"]),)
    ).fetchone()
    return Job(**job_view(job, str(run["id"]) if run else None)).model_dump(mode="json")
