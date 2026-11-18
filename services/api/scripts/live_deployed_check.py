"""Smoke test of the DEPLOYED system (Vercel web + Vercel API + GitHub Actions worker + Supabase).

    DEPLOYED_API_URL=... DEPLOYED_WEB_URL=... python scripts/live_deployed_check.py

Proves: the API is up; CORS allows only the web origin; the web bundle is configured with the
public Supabase URL/key and the API URL and contains no service key; security headers are set;
a real user can upload through the deployed API; the API's repository_dispatch wakes the worker
and ingestion finishes; dispatch-to-done latency is measured.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
import time
from pathlib import Path

import httpx

API = os.environ["DEPLOYED_API_URL"].rstrip("/")
WEB = os.environ["DEPLOYED_WEB_URL"].rstrip("/")
SB = os.environ["SUPABASE_URL"].rstrip("/")
ANON = os.environ["SUPABASE_ANON_KEY"]
SERVICE = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
RUN = os.environ.get("GITHUB_RUN_ID") or secrets.token_hex(4)
DEMO = (Path(__file__).resolve().parents[3] / "data/synthetic/SYNTHETIC_results_seed2026.csv").read_bytes()
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)
    return ok


def main() -> int:
    http = httpx.Client(timeout=60, follow_redirects=True)

    r = http.get(f"{API}/health")
    check("deployed API /health", r.status_code == 200 and r.json().get("status") == "ok", r.text[:80])

    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    ok_origin = http.options(f"{API}/uploads", headers={"Origin": WEB, **pre})
    bad_origin = http.options(f"{API}/uploads", headers={"Origin": "https://evil.example", **pre})
    check(
        "CORS allows the web app origin",
        ok_origin.headers.get("access-control-allow-origin") == WEB,
        f"got {ok_origin.headers.get('access-control-allow-origin')!r}",
    )
    check("CORS refuses other origins", bad_origin.headers.get("access-control-allow-origin") is None)

    page = http.get(f"{WEB}/")
    check("web app serves index", page.status_code == 200 and 'id="root"' in page.text, f"HTTP {page.status_code}")
    check(
        "web security headers", page.headers.get("x-frame-options") == "DENY", str(page.headers.get("x-frame-options"))
    )
    deep = http.get(f"{WEB}/projects/does-not-matter")
    check("deep links load the app (SPA rewrite)", deep.status_code == 200 and 'id="root"' in deep.text)
    bundle = ""
    for src in re.findall(r'src="(/assets/[^"]+\.js)"', page.text):
        bundle += http.get(f"{WEB}{src}").text
    host = re.sub(r"^https?://", "", SB)
    check("web bundle has Supabase URL and public key", host in bundle and ANON in bundle)
    check("web bundle points at the deployed API", re.sub(r"^https?://", "", API) in bundle)
    check("web bundle contains NO service key", SERVICE not in bundle and "service_role" not in bundle)

    # Real user through the deployed API
    email, pw = f"ci-{RUN}-deployed@example.com", secrets.token_urlsafe(18)
    svc = {"apikey": SERVICE, **({"Authorization": f"Bearer {SERVICE}"} if SERVICE.startswith("eyJ") else {})}
    http.post(
        f"{SB}/auth/v1/admin/users", headers=svc, json={"email": email, "password": pw, "email_confirm": True}
    ).raise_for_status()
    tok = http.post(
        f"{SB}/auth/v1/token?grant_type=password", headers={"apikey": ANON}, json={"email": email, "password": pw}
    ).json()["access_token"]
    user = {"apikey": ANON, "Authorization": f"Bearer {tok}"}
    uid = http.get(f"{SB}/auth/v1/user", headers=user).json()["id"]
    org = http.post(
        f"{SB}/rest/v1/rpc/create_organisation", headers=user, json={"p_name": f"ZZ CI deployed check {RUN}"}
    ).json()
    proj = http.post(
        f"{SB}/rest/v1/projects",
        headers={**user, "Prefer": "return=representation"},
        json={
            "org_id": org,
            "election_name": "SYNTHETIC deployed check",
            "election_year": 2027,
            "country": "Synthetica",
            "election_level": "other",
            "created_by": uid,
        },
    ).json()[0]["id"]

    bearer = {"Authorization": f"Bearer {tok}", "Origin": WEB}
    up = http.post(
        f"{API}/uploads",
        headers=bearer,
        json={
            "project_id": proj,
            "dataset_name": "SYNTHETIC demo",
            "original_filename": "SYNTHETIC_results_seed2026.csv",
            "size_bytes": len(DEMO),
            "content_type": "text/csv",
            "source_type": "synthetic",
            "source_description": "SYNTHETIC deployed smoke test",
        },
    )
    if not check("deployed POST /uploads", up.status_code == 201, f"HTTP {up.status_code} {up.text[:160]}"):
        return report()
    u = up.json()
    put = http.put(u["upload_url"], content=DEMO, headers={"Content-Type": "text/csv", "x-upsert": "false"})
    check("signed upload", put.status_code in (200, 201), f"HTTP {put.status_code}")
    t0 = time.monotonic()
    fin = http.post(f"{API}/dataset-versions/{u['dataset_version_id']}/finalize", headers=bearer)
    check("deployed finalize", fin.status_code == 200, f"HTTP {fin.status_code} in {time.monotonic() - t0:.1f}s")
    if fin.status_code != 200:
        return report()
    job_id = fin.json()["ingestion_job"]["id"]
    if os.environ.get("DATABASE_URL"):
        import psycopg

        with psycopg.connect(os.environ["DATABASE_URL"]) as db:
            res = db.execute("SELECT dispatch_result FROM public.processing_jobs WHERE id = %s", (job_id,)).fetchone()
        check(
            "API recorded a successful GitHub dispatch",
            bool(res) and res[0] == "ok",
            f"dispatch_result={res and res[0]}",
        )
    t0 = time.monotonic()
    status, first_running = "queued", None
    while time.monotonic() - t0 < 300 and status in ("queued", "running"):
        time.sleep(5)
        j = http.get(f"{API}/jobs/{job_id}", headers=bearer).json()
        status = j.get("status")
        if status == "running" and first_running is None:
            first_running = time.monotonic() - t0
    total = time.monotonic() - t0
    started = f"{first_running:.0f}s" if first_running is not None else "n/a"
    check(
        "worker woken by API dispatch; ingestion succeeded",
        status == "succeeded",
        f"status={status}; start latency ≈ {started}; finalize→done {total:.0f}s",
    )
    return report()


def report() -> int:
    lines = ["## Deployed system check", "", "| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {n} | {'PASS' if ok else 'FAIL'} | {d.replace('|', '/')} |" for n, ok, d in results]
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} checks passed")
    return 0 if results and all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
