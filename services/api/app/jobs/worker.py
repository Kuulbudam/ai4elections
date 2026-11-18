"""Job worker. Runs in GitHub Actions (ADR-0002) or locally.

    python -m app.jobs.worker --drain                 # reconcile, then run jobs until the queue is empty
    python -m app.jobs.worker --drain --max-jobs 1
    python -m app.jobs.worker --reconcile-only

Environment: DATABASE_URL, optional LEDGER_SCHEMA (default "public"), WORKER_ID.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import time
import uuid
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from app.jobs import ledger

log = logging.getLogger("ai4e.worker")


class JobContext:
    def __init__(self, conn: psycopg.Connection, job: dict[str, Any], worker_id: str, hb_interval: float):
        self.conn = conn
        self.job = job
        self.worker_id = worker_id
        self.hb_interval = hb_interval
        self._last_hb = 0.0

    def heartbeat(self, progress: float, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_hb < self.hb_interval:
            return
        if not ledger.heartbeat(self.conn, str(self.job["id"]), self.worker_id, progress):
            raise ledger.LostOwnership(str(self.job["id"]))
        self._last_hb = now


class JobError(Exception):
    def __init__(self, code: str, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


# --------------------------------------------------------------------------- spike job


def run_spike_sleep(ctx: JobContext) -> None:
    """Phase 0 spike: run for `duration_s` seconds with heartbeats, then write one output row."""
    job = ctx.job
    payload = job["payload"] or {}
    duration = float(payload.get("duration_s", 90))
    fail_attempts = int(payload.get("fail_attempts", 0))
    jid, attempt = str(job["id"]), job["attempt_count"]
    t = ledger._t  # noqa: SLF001 - same package

    ctx.conn.execute(
        sql.SQL("INSERT INTO {} (job_id, worker_id, attempt) VALUES (%s, %s, %s)").format(t("spike_executions")),
        (jid, ctx.worker_id, attempt),
    )
    started = time.monotonic()
    while (elapsed := time.monotonic() - started) < duration:
        ctx.heartbeat(elapsed / duration)
        time.sleep(min(0.5, duration - elapsed))
    if attempt <= fail_attempts:
        raise JobError("SPIKE_PLANNED_FAILURE", retryable=True)

    # Output and success are committed together: either both happen or neither does.
    with ctx.conn.transaction():
        ctx.conn.execute(
            sql.SQL(
                """
                INSERT INTO {} (job_id, attempt, result) VALUES (%s, %s, %s)
                ON CONFLICT (job_id) DO UPDATE
                    SET attempt = EXCLUDED.attempt, result = EXCLUDED.result, written_at = now()
                """
            ).format(t("spike_outputs")),
            (jid, attempt, Jsonb({"duration_s": duration, "worker_id": ctx.worker_id})),
        )
        ledger.mark_succeeded(ctx.conn, jid, ctx.worker_id)


def _run_ingest(ctx: JobContext) -> None:
    from app.jobs.ingest import run_ingest

    run_ingest(ctx)


def _run_validate(ctx: JobContext) -> None:
    from app.jobs.validate import run_validate

    run_validate(ctx)


HANDLERS: dict[str, Callable[[JobContext], None]] = {
    "spike_sleep": run_spike_sleep,
    "ingest": _run_ingest,
    "validate": _run_validate,
}

# --------------------------------------------------------------------------- loop


def process_one(conn: psycopg.Connection, worker_id: str, hb_interval: float = 5.0) -> dict | None:
    job = ledger.claim(conn, worker_id)
    if job is None:
        return None
    jid = str(job["id"])
    log.info("claimed job=%s type=%s attempt=%s", jid, job["job_type"], job["attempt_count"])
    handler = HANDLERS.get(job["job_type"])
    try:
        if handler is None:
            raise JobError("UNKNOWN_JOB_TYPE", retryable=False)
        ctx = JobContext(conn, job, worker_id, hb_interval)
        ctx.heartbeat(0.0, force=True)
        handler(ctx)
        log.info("succeeded job=%s", jid)
    except ledger.LostOwnership:
        log.warning("lost ownership of job=%s; another worker or reconciliation took it", jid)
    except JobError as exc:
        status = ledger.mark_failed_or_retry(conn, jid, worker_id, error_code=exc.code, retryable=exc.retryable)
        log.warning("job=%s error=%s -> %s", jid, exc.code, status)
        settle_failed_ingests(conn)
    except Exception:  # noqa: BLE001 - never leave a job silently running
        log.exception("unexpected error in job=%s", jid)
        status = ledger.mark_failed_or_retry(conn, jid, worker_id, error_code="INTERNAL", retryable=True)
        log.warning("job=%s -> %s", jid, status)
        settle_failed_ingests(conn)
    return ledger.get(conn, jid)


def settle_failed_ingests(conn: psycopg.Connection) -> int:
    """A version whose ingest job has finally failed must not look like it is still ingesting."""
    if ledger._schema() != "public":  # noqa: SLF001
        return 0
    cur = conn.execute(
        """
        UPDATE public.dataset_versions v SET status = 'ingest_failed'
        WHERE v.status IN ('uploaded', 'ingesting')
          AND EXISTS (SELECT 1 FROM public.processing_jobs j
                      WHERE j.target_id = v.id AND j.job_type = 'ingest' AND j.status = 'failed')
          AND NOT EXISTS (SELECT 1 FROM public.processing_jobs j
                          WHERE j.target_id = v.id AND j.job_type = 'ingest' AND j.status IN ('queued', 'running'))
        """
    )
    return cur.rowcount


def drain(conn: psycopg.Connection, worker_id: str, max_jobs: int | None, stale_seconds: int) -> int:
    counts = ledger.reconcile_stale(conn, stale_seconds)
    log.info("reconcile: %s", counts)
    settle_failed_ingests(conn)
    done = 0
    while max_jobs is None or done < max_jobs:
        if process_one(conn, worker_id) is None:
            break
        done += 1
    return done


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--drain", action="store_true")
    p.add_argument("--reconcile-only", action="store_true")
    p.add_argument("--max-jobs", type=int, default=None)
    p.add_argument("--stale-seconds", type=int, default=60)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker_id = os.environ.get("WORKER_ID") or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    with ledger.connect() as conn:
        if args.reconcile_only:
            log.info("reconcile: %s", ledger.reconcile_stale(conn, args.stale_seconds))
            return 0
        if args.drain:
            n = drain(conn, worker_id, args.max_jobs, args.stale_seconds)
            log.info("worker %s processed %d job(s)", worker_id, n)
            return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
