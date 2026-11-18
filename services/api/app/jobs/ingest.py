"""Ingestion job: stored CSV → raw_rows (every non-blank row, every cell as original text).

Atomic: raw rows, the version's header/row count/status and the job's success are committed in
one transaction, so a retry after any crash starts from a clean slate and never duplicates rows.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from psycopg.types.json import Jsonb

from app import deps
from app.ingest.csv_parse import ParseError, parse_csv
from app.jobs import ledger

if TYPE_CHECKING:
    from app.jobs.worker import JobContext


def _fail_version(ctx: JobContext, code: str) -> None:
    from app.jobs.worker import JobError

    ctx.conn.execute(
        "UPDATE public.dataset_versions SET status = 'ingest_failed' WHERE id = %s AND status <> 'ingested'",
        (str(ctx.job["target_id"]),),
    )
    raise JobError(code, retryable=False)


def run_ingest(ctx: JobContext) -> None:
    from app.jobs.worker import JobError

    job, conn = ctx.job, ctx.conn
    # Worker-side verification: the target must belong to the job's organisation and project.
    v = conn.execute(
        "SELECT * FROM public.dataset_versions WHERE id = %s AND org_id = %s AND project_id = %s",
        (str(job["target_id"]), str(job["org_id"]), str(job["project_id"])),
    ).fetchone()
    if v is None:
        raise JobError("JOB_TARGET_MISMATCH", retryable=False)
    if v["status"] == "ingested":  # already done (e.g. duplicate dispatch after success)
        with conn.transaction():
            ledger.mark_succeeded(conn, str(job["id"]), ctx.worker_id)
        return
    if v["sha256"] is None or v["finalized_at"] is None:
        raise JobError("VERSION_NOT_FINALIZED", retryable=False)

    conn.execute("UPDATE public.dataset_versions SET status = 'ingesting' WHERE id = %s", (str(v["id"]),))
    ctx.heartbeat(0.05, force=True)
    data = deps.get_storage().download(v["storage_path"])
    if data is None:
        raise JobError("STORED_FILE_MISSING", retryable=True)
    if hashlib.sha256(data).hexdigest() != v["sha256"]:
        _fail_version(ctx, "INTEGRITY_MISMATCH")  # stored bytes differ from the finalized hash
    ctx.heartbeat(0.2, force=True)
    try:
        parsed = parse_csv(data)
    except ParseError as exc:
        conn.execute(
            "UPDATE public.dataset_versions SET header = %s WHERE id = %s",
            (Jsonb({"error_code": exc.code, "error": exc.message}), str(v["id"])),
        )
        _fail_version(ctx, exc.code)
    ctx.heartbeat(0.5, force=True)

    with conn.transaction():
        with conn.cursor().copy(
            "COPY public.raw_rows (org_id, dataset_version_id, source_row_number, cells) FROM STDIN"
        ) as copy:
            for row_no, cells in parsed.rows:
                copy.write_row((str(v["org_id"]), str(v["id"]), row_no, Jsonb(cells)))
        conn.execute(
            "UPDATE public.dataset_versions SET header = %s, row_count = %s, status = 'ingested' WHERE id = %s",
            (Jsonb(parsed.header_meta()), len(parsed.rows), str(v["id"])),
        )
        ledger.mark_succeeded(conn, str(job["id"]), ctx.worker_id)
