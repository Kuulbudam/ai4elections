"""90-second worker spike (master prompt Section 8). Runs against DATABASE_URL in schema `spike`.

    python spike/run_spike.py [--duration 90] [--stale 20]

Scenarios:
  1. normal   - a 90 s job runs in a separate worker process, heartbeats, persists, succeeds.
  2. killed   - a worker is SIGKILLed mid-run; reconciliation requeues; a second worker completes it.
  3. dispatch - three workers start at once for one job; it executes exactly once.
Prints a Markdown summary (also appended to $GITHUB_STEP_SUMMARY when running in Actions).
Exit code 0 only if every scenario passes.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
os.environ.setdefault("LEDGER_SCHEMA", "spike")

from app.jobs import ledger  # noqa: E402


def worker_proc(name: str) -> subprocess.Popen:
    env = {**os.environ, "WORKER_ID": name}
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "app.jobs.worker", "--drain", "--max-jobs", "1", "--stale-seconds", "3600"],
        cwd=API_DIR,
        env=env,
    )


def enqueue(conn, duration: float) -> str:
    job = ledger.enqueue(
        conn,
        job_type="spike_sleep",
        target_id=str(uuid.uuid4()),
        input_fingerprint=uuid.uuid4().hex,
        payload={"duration_s": duration},
    ).job
    return str(job["id"])


def count(conn, table: str, job_id: str) -> int:
    q = f"SELECT count(*) AS n FROM spike.{table} WHERE job_id = %s"  # noqa: S608 - fixed table names
    return conn.execute(q, (job_id,)).fetchone()["n"]


def wait_status(conn, job_id: str, status: str, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if ledger.get(conn, job_id)["status"] == status:
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=90)
    ap.add_argument("--stale", type=int, default=20)
    args = ap.parse_args()
    results: list[tuple[str, bool, str]] = []

    with ledger.connect() as conn:
        conn.execute((API_DIR / "spike" / "schema.sql").read_text())

        # 1. normal completion
        jid = enqueue(conn, args.duration)
        t0 = time.monotonic()
        rc = worker_proc("spike-normal").wait()
        took = time.monotonic() - t0
        job = ledger.get(conn, jid)
        ok = rc == 0 and job["status"] == "succeeded" and count(conn, "spike_outputs", jid) == 1
        results.append(("normal 90 s run", ok, f"status={job['status']} wall={took:.1f}s"))

        # 2. killed mid-run, reconciled, retried
        jid = enqueue(conn, args.duration)
        p = worker_proc("spike-killed")
        started = wait_status(conn, jid, "running", 60)
        time.sleep(args.duration / 3)
        p.send_signal(signal.SIGKILL)
        p.wait()
        time.sleep(args.stale + 2)
        rec = ledger.reconcile_stale(conn, args.stale)
        requeued = ledger.get(conn, jid)["status"] == "queued"
        rc = worker_proc("spike-rescue").wait()
        job = ledger.get(conn, jid)
        ok = (
            started
            and rec["requeued"] >= 1
            and requeued
            and rc == 0
            and job["status"] == "succeeded"
            and job["attempt_count"] == 2
            and count(conn, "spike_outputs", jid) == 1
        )
        results.append(("kill → reconcile → retry", ok, f"reconcile={rec} attempts={job['attempt_count']}"))

        # 3. duplicate dispatch
        jid = enqueue(conn, min(10.0, args.duration))
        procs = [worker_proc(f"spike-dup-{i}") for i in range(3)]
        rcs = [pr.wait() for pr in procs]
        execs = count(conn, "spike_executions", jid)
        job = ledger.get(conn, jid)
        ok = rcs == [0, 0, 0] and execs == 1 and job["status"] == "succeeded"
        results.append(("duplicate dispatch → one execution", ok, f"executions={execs}"))

    lines = [
        "## Worker spike results",
        "",
        f"Host: `{os.environ.get('SPIKE_HOST', 'local')}`, duration {args.duration:.0f}s",
        "",
        "| Scenario | Result | Detail |",
        "|---|---|---|",
    ]
    lines += [f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |" for name, ok, detail in results]
    report = "\n".join(lines)
    print(report)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as fh:
            fh.write(report + "\n")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
