"""PostgreSQL job ledger (ADR-0003).

All functions take an autocommit psycopg connection. Each public function is one atomic
statement or one explicit transaction, so a crash at any point leaves the ledger consistent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

LIVE_STATUSES = ("queued", "running")


def _schema() -> str:
    return os.environ.get("LEDGER_SCHEMA", "public")


def _t(name: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(_schema()), sql.Identifier(name))


@dataclass(frozen=True)
class EnqueueResult:
    job: dict[str, Any]
    created: bool


def connect(dsn: str | None = None) -> psycopg.Connection:
    dsn = dsn or os.environ["DATABASE_URL"]
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)


def enqueue(
    conn: psycopg.Connection,
    *,
    job_type: str,
    target_id: str,
    input_fingerprint: str,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 3,
) -> EnqueueResult:
    """Insert a queued job, or return the existing live job for the same (type, target, input)."""
    with conn.transaction():
        row = conn.execute(
            sql.SQL(
                """
                INSERT INTO {jobs} (job_type, target_id, input_fingerprint, payload, max_attempts)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (job_type, target_id, input_fingerprint)
                    WHERE status IN ('queued', 'running') DO NOTHING
                RETURNING *
                """
            ).format(jobs=_t("processing_jobs")),
            (job_type, target_id, input_fingerprint, Jsonb(payload or {}), max_attempts),
        ).fetchone()
        if row is not None:
            return EnqueueResult(job=row, created=True)
        existing = conn.execute(
            sql.SQL(
                """
                SELECT * FROM {jobs}
                WHERE job_type = %s AND target_id = %s AND input_fingerprint = %s
                  AND status IN ('queued', 'running')
                """
            ).format(jobs=_t("processing_jobs")),
            (job_type, target_id, input_fingerprint),
        ).fetchone()
    if existing is None:  # the live job finished between the two statements; try once more
        return enqueue(
            conn,
            job_type=job_type,
            target_id=target_id,
            input_fingerprint=input_fingerprint,
            payload=payload,
            max_attempts=max_attempts,
        )
    return EnqueueResult(job=existing, created=False)


def claim(conn: psycopg.Connection, worker_id: str) -> dict[str, Any] | None:
    """Atomically claim the oldest queued job. Two workers can never claim the same job."""
    return conn.execute(
        sql.SQL(
            """
            UPDATE {jobs} SET
                status = 'running',
                started_at = COALESCE(started_at, now()),
                heartbeat_at = now(),
                worker_id = %s,
                attempt_count = attempt_count + 1,
                progress = 0,
                error_code = NULL,
                retryable = NULL
            WHERE id = (
                SELECT id FROM {jobs}
                WHERE status = 'queued'
                ORDER BY created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING *
            """
        ).format(jobs=_t("processing_jobs")),
        (worker_id,),
    ).fetchone()


def heartbeat(conn: psycopg.Connection, job_id: str, worker_id: str, progress: float) -> bool:
    """Record liveness. Returns False if this worker no longer owns the job (it must stop)."""
    progress = max(0.0, min(1.0, progress))
    cur = conn.execute(
        sql.SQL(
            """
            UPDATE {jobs} SET heartbeat_at = now(), progress = %s
            WHERE id = %s AND worker_id = %s AND status = 'running'
            """
        ).format(jobs=_t("processing_jobs")),
        (progress, job_id, worker_id),
    )
    return cur.rowcount == 1


class LostOwnership(RuntimeError):
    """Raised when a worker tries to finish a job it no longer owns."""


def mark_succeeded(conn: psycopg.Connection, job_id: str, worker_id: str) -> None:
    """Must be called inside the same transaction that wrote the job's outputs."""
    cur = conn.execute(
        sql.SQL(
            """
            UPDATE {jobs} SET status = 'succeeded', progress = 1, finished_at = now()
            WHERE id = %s AND worker_id = %s AND status = 'running'
            """
        ).format(jobs=_t("processing_jobs")),
        (job_id, worker_id),
    )
    if cur.rowcount != 1:
        raise LostOwnership(job_id)


def mark_failed_or_retry(
    conn: psycopg.Connection, job_id: str, worker_id: str, *, error_code: str, retryable: bool
) -> str:
    """Return the job to the queue if it is retryable and has attempts left; otherwise fail it."""
    row = conn.execute(
        sql.SQL(
            """
            UPDATE {jobs} SET
                status = CASE WHEN %(retryable)s AND attempt_count < max_attempts
                              THEN 'queued' ELSE 'failed' END,
                finished_at = CASE WHEN %(retryable)s AND attempt_count < max_attempts
                                   THEN NULL ELSE now() END,
                worker_id = NULL,
                error_code = %(code)s,
                retryable = %(retryable)s
            WHERE id = %(id)s AND worker_id = %(worker)s AND status = 'running'
            RETURNING status
            """
        ).format(jobs=_t("processing_jobs")),
        {"retryable": retryable, "code": error_code, "id": job_id, "worker": worker_id},
    ).fetchone()
    if row is None:
        raise LostOwnership(job_id)
    return row["status"]


def reconcile_stale(conn: psycopg.Connection, stale_seconds: int = 60) -> dict[str, int]:
    """Requeue (or fail, if out of attempts) running jobs whose heartbeat is too old."""
    rows = conn.execute(
        sql.SQL(
            """
            UPDATE {jobs} SET
                status = CASE WHEN attempt_count < max_attempts THEN 'queued' ELSE 'failed' END,
                finished_at = CASE WHEN attempt_count < max_attempts THEN NULL ELSE now() END,
                worker_id = NULL,
                error_code = CASE WHEN attempt_count < max_attempts
                                  THEN 'STALE_HEARTBEAT_REQUEUED' ELSE 'STALE_HEARTBEAT_EXHAUSTED' END,
                retryable = attempt_count < max_attempts
            WHERE status = 'running'
              AND heartbeat_at < now() - make_interval(secs => %s)
            RETURNING status
            """
        ).format(jobs=_t("processing_jobs")),
        (stale_seconds,),
    ).fetchall()
    return {
        "requeued": sum(1 for r in rows if r["status"] == "queued"),
        "failed": sum(1 for r in rows if r["status"] == "failed"),
    }


def get(conn: psycopg.Connection, job_id: str) -> dict[str, Any] | None:
    return conn.execute(
        sql.SQL("SELECT * FROM {jobs} WHERE id = %s").format(jobs=_t("processing_jobs")), (job_id,)
    ).fetchone()
