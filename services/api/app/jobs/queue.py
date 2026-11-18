"""Queueing application jobs in public.processing_jobs (tenant-scoped ledger)."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb


def enqueue_job(
    conn: psycopg.Connection,
    *,
    org_id: str,
    project_id: str,
    job_type: str,
    target_id: str,
    input_fingerprint: str,
    created_by: str,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 3,
) -> tuple[dict[str, Any], bool]:
    """Insert a queued job or return the live one for the same (type, target, input). Call inside a tx."""
    row = conn.execute(
        """
        INSERT INTO public.processing_jobs
            (org_id, project_id, job_type, target_id, input_fingerprint, payload, created_by, max_attempts)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (job_type, target_id, input_fingerprint) WHERE status IN ('queued', 'running') DO NOTHING
        RETURNING *
        """,
        (
            org_id,
            project_id,
            job_type,
            target_id,
            input_fingerprint,
            Jsonb(payload or {}),
            created_by,
            max_attempts,
        ),
    ).fetchone()
    if row is not None:
        return row, True
    existing = conn.execute(
        """
        SELECT * FROM public.processing_jobs
        WHERE job_type = %s AND target_id = %s AND input_fingerprint = %s AND status IN ('queued', 'running')
        """,
        (job_type, target_id, input_fingerprint),
    ).fetchone()
    if existing is None:  # finished in between; queue a fresh one
        return enqueue_job(
            conn,
            org_id=org_id,
            project_id=project_id,
            job_type=job_type,
            target_id=target_id,
            input_fingerprint=input_fingerprint,
            created_by=created_by,
            payload=payload,
            max_attempts=max_attempts,
        )
    return existing, False


def job_view(row: dict[str, Any], analytical_run_id: str | None = None) -> dict[str, Any]:
    """Shape a processing_jobs row as the contract's Job schema."""
    return {
        "id": str(row["id"]),
        "job_type": row["job_type"],
        "status": row["status"],
        "progress": float(row["progress"]),
        "attempt_count": row["attempt_count"],
        "max_attempts": row["max_attempts"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "heartbeat_at": row["heartbeat_at"],
        "error_code": row["error_code"],
        "retryable": row["retryable"],
        "analytical_run_id": analytical_run_id,
    }
