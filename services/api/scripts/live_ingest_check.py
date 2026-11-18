"""Live upload → finalize → ingest → mapping check against the real Supabase project.

Runs the real FastAPI app in-process (real JWT verification via the project's JWKS, real
database, real private Storage with a signed upload URL) and the real worker code, using the
committed SYNTHETIC demo file. Needs SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY,
DATABASE_URL. Leaves one "ZZ CI ingest check" organisation per run (append-only by design).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
import time
from pathlib import Path

import httpx
import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
os.environ["LEDGER_SCHEMA"] = "public"
os.environ.pop("GITHUB_DISPATCH_TOKEN", None)  # the check drains the queue itself

from app.jobs import worker  # noqa: E402
from app.main import app  # noqa: E402

URL = os.environ["SUPABASE_URL"].rstrip("/")
ANON = os.environ["SUPABASE_ANON_KEY"]
SERVICE = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
RUN = os.environ.get("GITHUB_RUN_ID") or secrets.token_hex(4)
DEMO = (API_DIR.parents[1] / "data/synthetic/SYNTHETIC_results_seed2026.csv").read_bytes()
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)
    return ok


def svc_headers() -> dict[str, str]:
    h = {"apikey": SERVICE}
    if SERVICE.startswith("eyJ"):
        h["Authorization"] = f"Bearer {SERVICE}"
    return h


def make_user(http: httpx.Client, label: str) -> dict:
    email, pw = f"ci-{RUN}-{label}@example.com", secrets.token_urlsafe(18)
    r = http.post(
        f"{URL}/auth/v1/admin/users",
        headers=svc_headers(),
        json={"email": email, "password": pw, "email_confirm": True},
    )
    r.raise_for_status()
    t = http.post(
        f"{URL}/auth/v1/token?grant_type=password", headers={"apikey": ANON}, json={"email": email, "password": pw}
    )
    t.raise_for_status()
    return {"id": r.json()["id"], "token": t.json()["access_token"]}


def rest(http, user, method, path, **kw):
    return http.request(
        method, f"{URL}/rest/v1/{path}", headers={"apikey": ANON, "Authorization": f"Bearer {user['token']}"}, **kw
    )


def main() -> int:
    http = httpx.Client(timeout=60)
    api = TestClient(app, raise_server_exceptions=False)
    a, b = make_user(http, "ingest-a"), make_user(http, "ingest-b")
    org = rest(http, a, "POST", "rpc/create_organisation", json={"p_name": f"ZZ CI ingest check {RUN}"}).json()
    rest(http, b, "POST", "rpc/create_organisation", json={"p_name": f"ZZ CI ingest check {RUN} B"})
    proj = rest(
        http,
        a,
        "POST",
        "projects",
        headers={"apikey": ANON, "Authorization": f"Bearer {a['token']}", "Prefer": "return=representation"},
        json={
            "org_id": org,
            "election_name": "SYNTHETIC CI Election",
            "election_year": 2027,
            "country": "Synthetica",
            "election_level": "other",
            "created_by": a["id"],
        },
    ).json()[0]["id"]
    auth_a = {"Authorization": f"Bearer {a['token']}"}

    # 1. register upload (real JWT verification via JWKS)
    r = api.post(
        "/uploads",
        headers={**auth_a, "Idempotency-Key": f"ci-{RUN}"},
        json={
            "project_id": proj,
            "dataset_name": "SYNTHETIC demo results",
            "original_filename": "SYNTHETIC_results_seed2026.csv",
            "size_bytes": len(DEMO),
            "content_type": "text/csv",
            "source_type": "synthetic",
            "source_description": "SYNTHETIC demo data (tools/synthetic/generate.py, seed 2026)",
        },
    )
    if not check(
        "POST /uploads with a real Supabase token", r.status_code == 201, f"HTTP {r.status_code} {r.text[:200]}"
    ):
        return report()
    up = r.json()

    # 2. upload the bytes straight to private storage with the signed URL (as the browser does)
    t0 = time.monotonic()
    put = http.put(up["upload_url"], content=DEMO, headers={"Content-Type": "text/csv", "x-upsert": "false"})
    check(
        "signed URL upload to private bucket",
        put.status_code in (200, 201),
        f"HTTP {put.status_code} in {time.monotonic() - t0:.1f}s",
    )
    again = http.put(up["upload_url"], content=b"overwrite attempt", headers={"Content-Type": "text/csv"})
    check("stored original cannot be overwritten", again.status_code >= 400, f"HTTP {again.status_code}")

    # 3. finalize: server computes SHA-256 from the stored bytes
    t0 = time.monotonic()
    f = api.post(f"/dataset-versions/{up['dataset_version_id']}/finalize", headers=auth_a)
    fin = f.json() if f.status_code == 200 else {}
    check(
        "finalize computes the server-side SHA-256",
        f.status_code == 200 and fin.get("sha256") == hashlib.sha256(DEMO).hexdigest(),
        f"HTTP {f.status_code} in {time.monotonic() - t0:.1f}s",
    )

    # 4. ingestion by the real worker code
    t0 = time.monotonic()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row) as db:
        worker.drain(db, f"ci-{RUN}", max_jobs=5, stale_seconds=600)
        took = time.monotonic() - t0
        v = db.execute("SELECT * FROM public.dataset_versions WHERE id=%s", (up["dataset_version_id"],)).fetchone()
        n = db.execute(
            "SELECT count(*) AS n FROM public.raw_rows WHERE dataset_version_id=%s", (up["dataset_version_id"],)
        ).fetchone()["n"]
    check(
        "12,023 rows ingested and preserved",
        v["status"] == "ingested" and n == 12_023 == v["row_count"],
        f"{n} rows in {took:.1f}s",
    )

    job = api.get(f"/jobs/{fin.get('ingestion_job', {}).get('id')}", headers=auth_a)
    check(
        "GET /jobs shows succeeded", job.status_code == 200 and job.json().get("status") == "succeeded", job.text[:120]
    )
    other = api.get(
        f"/jobs/{fin.get('ingestion_job', {}).get('id')}", headers={"Authorization": f"Bearer {b['token']}"}
    )
    check("another organisation gets 404 for the job", other.status_code == 404, f"HTTP {other.status_code}")
    forged = api.get(f"/jobs/{fin.get('ingestion_job', {}).get('id')}", headers={"Authorization": "Bearer not-a-jwt"})
    check("invalid token rejected", forged.status_code == 401, f"HTTP {forged.status_code}")

    # 5. mapping
    mapping = {
        "schema_version": "1.0.0",
        "fields": {
            "pu_code": "PU Code",
            "state": "State",
            "lga": "LGA",
            "ward": "Ward",
            "registered_voters": "Registered Voters",
            "accredited_voters": "Accredited Voters",
            "valid_votes": "Valid Votes",
            "rejected_votes": "Rejected Votes",
            "total_cast": "Total Votes Cast",
        },  # fmt: skip
        "party_columns": [{"source_column": f"Votes P{c}", "party_code": f"P{c}"} for c in "ABCDE"],
    }
    m = api.put(f"/dataset-versions/{up['dataset_version_id']}/mapping", headers=auth_a, json=mapping)
    check("mapping saved with no issues", m.status_code == 200 and m.json().get("issues") == [], m.text[:160])
    return report()


def report() -> int:
    lines = ["## Live ingestion check", "", "| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {n} | {'PASS' if ok else 'FAIL'} | {d.replace('|', '/')} |" for n, ok, d in results]
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} checks passed")
    return 0 if results and all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
