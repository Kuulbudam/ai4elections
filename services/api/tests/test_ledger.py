"""Job ledger + worker tests against a real PostgreSQL (TEST_DATABASE_URL). Skipped if not set."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

API_DIR = Path(__file__).resolve().parents[1]
SCHEMA_SQL = (API_DIR / "spike" / "schema.sql").read_text()


@pytest.fixture()
def conn(monkeypatch):
    monkeypatch.setenv("LEDGER_SCHEMA", "spike")
    from app.jobs import ledger

    c = ledger.connect(DSN)
    c.execute("DROP SCHEMA IF EXISTS spike CASCADE")
    c.execute(SCHEMA_SQL)
    yield c
    c.close()


def _enqueue(conn, **payload):
    from app.jobs import ledger

    max_attempts = payload.pop("max_attempts", 3)
    job_type = payload.pop("job_type", "spike_sleep")
    return ledger.enqueue(
        conn,
        job_type=job_type,
        target_id=str(uuid.uuid4()),
        input_fingerprint="fp",
        payload=payload,
        max_attempts=max_attempts,
    ).job


def _count(conn, table, job_id):
    return conn.execute(f"SELECT count(*) AS n FROM spike.{table} WHERE job_id = %s", (job_id,)).fetchone()["n"]


def test_duplicate_enqueue_returns_existing_job(conn):
    from app.jobs import ledger

    target = str(uuid.uuid4())
    a = ledger.enqueue(conn, job_type="validate", target_id=target, input_fingerprint="x")
    b = ledger.enqueue(conn, job_type="validate", target_id=target, input_fingerprint="x")
    assert a.created and not b.created
    assert a.job["id"] == b.job["id"]


def test_concurrent_claims_never_overlap(conn):
    from app.jobs import ledger

    for _ in range(40):
        _enqueue(conn, duration_s=0)
    claimed: list[str] = []
    lock = threading.Lock()

    def claimer(n: int) -> None:
        with ledger.connect(DSN) as c:
            while (job := ledger.claim(c, f"w{n}")) is not None:
                with lock:
                    claimed.append(str(job["id"]))

    threads = [threading.Thread(target=claimer, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == 40
    assert len(set(claimed)) == 40


def test_spike_job_completes_and_persists(conn):
    from app.jobs import worker

    job = _enqueue(conn, duration_s=1.5)
    final = worker.process_one(conn, "w1", hb_interval=0.2)
    assert final["status"] == "succeeded"
    assert final["progress"] == 1
    assert final["finished_at"] is not None
    assert _count(conn, "spike_outputs", job["id"]) == 1
    assert _count(conn, "spike_executions", job["id"]) == 1


def test_retry_after_failure_does_not_duplicate_output(conn):
    from app.jobs import worker

    job = _enqueue(conn, duration_s=0.2, fail_attempts=1)
    first = worker.process_one(conn, "w1", hb_interval=0.1)
    assert first["status"] == "queued" and first["error_code"] == "SPIKE_PLANNED_FAILURE"
    second = worker.process_one(conn, "w1", hb_interval=0.1)
    assert second["status"] == "succeeded" and second["attempt_count"] == 2
    assert _count(conn, "spike_outputs", job["id"]) == 1


def test_failure_never_appears_succeeded(conn):
    from app.jobs import worker

    job = _enqueue(conn, duration_s=0, fail_attempts=5, max_attempts=2)
    worker.process_one(conn, "w1")
    final = worker.process_one(conn, "w1")
    assert final["status"] == "failed"
    assert final["finished_at"] is not None
    assert _count(conn, "spike_outputs", job["id"]) == 0


def test_unknown_job_type_fails_without_retry(conn):
    from app.jobs import worker

    _enqueue(conn, job_type="nope")
    final = worker.process_one(conn, "w1")
    assert final["status"] == "failed"
    assert final["error_code"] == "UNKNOWN_JOB_TYPE"
    assert final["retryable"] is False
    assert final["attempt_count"] == 1


def _start_worker_process() -> subprocess.Popen:
    env = {**os.environ, "DATABASE_URL": DSN, "LEDGER_SCHEMA": "spike", "WORKER_ID": "killed-worker"}
    return subprocess.Popen(
        [sys.executable, "-m", "app.jobs.worker", "--drain", "--max-jobs", "1"],
        cwd=API_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_running(conn, job_id, timeout=15.0):
    from app.jobs import ledger

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ledger.get(conn, job_id)["status"] == "running":
            return
        time.sleep(0.1)
    raise AssertionError("worker never started the job")


def test_killed_worker_is_reconciled_and_retried_once(conn):
    from app.jobs import ledger, worker

    job = _enqueue(conn, duration_s=4)
    proc = _start_worker_process()
    _wait_running(conn, job["id"])
    time.sleep(1)
    proc.send_signal(signal.SIGKILL)  # simulate runner timeout / crash
    proc.wait()
    assert ledger.get(conn, job["id"])["status"] == "running"  # nobody told the ledger

    time.sleep(2.2)
    assert ledger.reconcile_stale(conn, stale_seconds=2) == {"requeued": 1, "failed": 0}
    requeued = ledger.get(conn, job["id"])
    assert requeued["status"] == "queued" and requeued["error_code"] == "STALE_HEARTBEAT_REQUEUED"

    final = worker.process_one(conn, "rescue-worker", hb_interval=0.5)
    assert final["status"] == "succeeded" and final["attempt_count"] == 2
    assert _count(conn, "spike_outputs", job["id"]) == 1
    assert _count(conn, "spike_executions", job["id"]) == 2  # one killed + one completed


def test_stale_job_out_of_attempts_is_failed(conn):
    from app.jobs import ledger

    job = _enqueue(conn, duration_s=4, max_attempts=1)
    proc = _start_worker_process()
    _wait_running(conn, job["id"])
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    time.sleep(2.2)
    assert ledger.reconcile_stale(conn, stale_seconds=2) == {"requeued": 0, "failed": 1}
    final = ledger.get(conn, job["id"])
    assert final["status"] == "failed" and final["error_code"] == "STALE_HEARTBEAT_EXHAUSTED"
    assert _count(conn, "spike_outputs", job["id"]) == 0


def test_duplicate_dispatch_executes_once(conn):
    from app.jobs import ledger, worker

    job = _enqueue(conn, duration_s=1.5)
    results = []

    def run(name: str) -> None:
        with ledger.connect(DSN) as c:
            results.append(worker.drain(c, name, max_jobs=None, stale_seconds=60))

    threads = [threading.Thread(target=run, args=(f"dispatch-{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [0, 0, 1]
    assert _count(conn, "spike_executions", job["id"]) == 1
    assert ledger.get(conn, job["id"])["status"] == "succeeded"


def test_worker_that_lost_ownership_cannot_finish(conn):
    from app.jobs import ledger

    job = _enqueue(conn, duration_s=0)
    ledger.claim(conn, "slow-worker")
    conn.execute(
        "UPDATE spike.processing_jobs SET heartbeat_at = now() - interval '5 minutes' WHERE id = %s",
        (job["id"],),
    )
    ledger.reconcile_stale(conn, stale_seconds=60)
    ledger.claim(conn, "new-worker")
    assert ledger.heartbeat(conn, str(job["id"]), "slow-worker", 0.5) is False
    with pytest.raises(ledger.LostOwnership):
        ledger.mark_succeeded(conn, str(job["id"]), "slow-worker")
    assert ledger.get(conn, job["id"])["worker_id"] == "new-worker"
