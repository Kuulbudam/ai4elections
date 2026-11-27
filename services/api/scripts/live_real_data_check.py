"""Live check with REAL data: CCIJ/VEZA 2023 presidential polling-unit results for Plateau.

Downloads the source at run time (tools/real_data/prepare_ccij.py; nothing is committed), then
runs the same path a user takes in production: signed upload to private storage → finalize →
ingest → reference list → mapping (schema 1.1.0, partial party list, no rejected/total cast) →
validation by the real worker code. Compares the stored validation summary with the engine run
locally on the same bytes, so the deployed database, storage and worker are shown to give the
same answer as the pure engine.

Needs SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, DATABASE_URL.
Leaves one "ZZ CI real-data check" organisation per run (append-only by design).
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import sys
import tempfile
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

API_DIR = Path(__file__).resolve().parents[1]
REPO = API_DIR.parents[1]
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(REPO / "tools" / "real_data"))

import live_ingest_check as L  # noqa: E402
import prepare_ccij  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.analysis.engine import Unit, analyse  # noqa: E402
from app.ingest.csv_parse import parse_csv  # noqa: E402
from app.jobs import worker  # noqa: E402
from app.main import app  # noqa: E402
from app.validation.engine import validate  # noqa: E402

MAPPING = {
    "schema_version": "1.1.0",
    "fields": {
        "pu_code": "PU Code",
        "state": "State",
        "lga": "LGA",
        "ward": "Ward",
        "registered_voters": "Registered Voters",
        "accredited_voters": "Accredited Voters",
        "valid_votes": "Valid Votes",
        "latitude": "Latitude",
        "longitude": "Longitude",
    },
    "party_columns": [{"source_column": f"Votes {p}", "party_code": p} for p in prepare_ccij.PARTIES],
    "party_columns_complete": False,
}


def main() -> int:
    check = L.check
    with tempfile.TemporaryDirectory() as d:
        rc = prepare_ccij.main(["--state", "PLATEAU", "--out-dir", d])
        if not check("CCIJ source downloaded and converted", rc == 0):
            return L.report()
        data = (Path(d) / "REAL_CCIJ_2023_presidential_plateau.csv").read_bytes()
        ref = list(csv.reader(io.StringIO((Path(d) / "REAL_CCIJ_2023_reference_plateau.csv").read_text())))[1:]

    parsed = parse_csv(data)
    vres = validate(parsed.columns, [(f"r{n}", n, c) for n, c in parsed.rows], MAPPING, [tuple(u) for u in ref])
    expected = vres.summary()
    expected_analysis = analyse(
        [
            Unit(r.raw_row_id, r.source_row_number, r.values["pu_code"], r.values["state"], r.values["lga"],
                 r.values["ward"], r.values["registered_voters"], r.values["accredited_voters"],
                 r.values["valid_votes"], r.party_votes, r.row_status, r.excluded_from, r.exclusion_reasons)
            for r in vres.records
        ],
        [i.__dict__ for i in vres.issues],
    ).summary  # fmt: skip

    import httpx

    http = httpx.Client(timeout=120)
    api = TestClient(app, raise_server_exceptions=False)
    a = L.make_user(http, "real")
    org = L.rest(http, a, "POST", "rpc/create_organisation", json={"p_name": f"ZZ CI real-data check {L.RUN}"}).json()
    proj = L.rest(
        http,
        a,
        "POST",
        "projects",
        extra_headers={"Prefer": "return=representation"},
        json={
            "org_id": org,
            "election_name": "2023 Presidential Election (Plateau, CCIJ/VEZA data)",
            "election_year": 2023,
            "country": "Nigeria",
            "election_level": "presidential",
            "created_by": a["id"],
        },
    ).json()[0]["id"]
    units = L.rest(
        http,
        a,
        "POST",
        "admin_units",
        json=[
            {"org_id": org, "project_id": proj, "state": s, "lga": lg, "ward": w, "created_by": a["id"]}
            for s, lg, w in ref
        ],
    )
    check("reference list loaded", units.status_code in (200, 201), f"{len(ref)} wards, HTTP {units.status_code}")

    auth = {"Authorization": f"Bearer {a['token']}"}
    r = api.post(
        "/uploads",
        headers={**auth, "Idempotency-Key": f"ci-real-{L.RUN}"},
        json={
            "project_id": proj,
            "dataset_name": "2023 presidential polling-unit results, Plateau (CCIJ/VEZA)",
            "original_filename": "REAL_CCIJ_2023_presidential_plateau.csv",
            "size_bytes": len(data),
            "content_type": "text/csv",
            "source_type": "observer_collation",
            "source_description": prepare_ccij.ATTRIBUTION[:1000],
        },
    )
    if not check("POST /uploads", r.status_code == 201, f"HTTP {r.status_code} {r.text[:200]}"):
        return L.report()
    up = r.json()
    put = http.put(up["upload_url"], content=data, headers={"Content-Type": "text/csv", "x-upsert": "false"})
    check("signed upload", put.status_code in (200, 201), f"HTTP {put.status_code}, {len(data):,} bytes")
    f = api.post(f"/dataset-versions/{up['dataset_version_id']}/finalize", headers=auth)
    check("finalize SHA-256", f.status_code == 200 and f.json().get("sha256") == hashlib.sha256(data).hexdigest())

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row) as db:
        worker.drain(db, f"ci-real-{L.RUN}", max_jobs=None, stale_seconds=600)
        v = db.execute(
            "SELECT status, row_count FROM public.dataset_versions WHERE id=%s", (up["dataset_version_id"],)
        ).fetchone()
    check(
        "4,989 Plateau polling units ingested",
        v["status"] == "ingested" and v["row_count"] == len(parsed.rows) == 4989,
        str(v),
    )

    m = api.put(f"/dataset-versions/{up['dataset_version_id']}/mapping", headers=auth, json=MAPPING)
    check(
        "schema 1.1.0 mapping with partial party list saved",
        m.status_code == 200 and m.json().get("issues") == [],
        m.text[:200],
    )
    j = api.post("/jobs", headers=auth, json={"job_type": "validate", "dataset_version_id": up["dataset_version_id"]})
    check("POST /jobs (validate)", j.status_code == 202, f"HTTP {j.status_code}")
    t0 = time.monotonic()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row) as db:
        worker.drain(db, f"ci-real-{L.RUN}-v", max_jobs=None, stale_seconds=600)
        took = time.monotonic() - t0
        s = (
            db.execute(
                "SELECT validation_summary FROM public.dataset_versions WHERE id=%s", (up["dataset_version_id"],)
            ).fetchone()["validation_summary"]
            or {}
        )
    same = {k: s.get(k) for k in expected} == expected
    check(
        "deployed validation matches the engine exactly",
        same and s.get("ruleset_version") == "1.2.0",
        f"{took:.1f}s; issues {s.get('issues_by_rule')}; rows {s.get('rows_by_rule')}; status {s.get('row_status')}",
    )
    ja = api.post("/jobs", headers=auth, json={"job_type": "analyse", "dataset_version_id": up["dataset_version_id"]})
    check("POST /jobs (analyse)", ja.status_code == 202, f"HTTP {ja.status_code}")
    t0 = time.monotonic()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row) as db:
        worker.drain(db, f"ci-real-{L.RUN}-a", max_jobs=None, stale_seconds=600)
        took = time.monotonic() - t0
        run = db.execute(
            "SELECT r.summary FROM public.analytical_runs r JOIN public.dataset_versions v "
            "ON v.latest_analysis_run_id = r.id WHERE v.id=%s",
            (up["dataset_version_id"],),
        ).fetchone()
    got = {k: v for k, v in ((run or {}).get("summary") or {}).items() if k != "content_hash"}
    check(
        "deployed analysis matches the engine exactly",
        got == expected_analysis,
        f"{took:.1f}s; statistical {got.get('statistical_findings')}, data quality {got.get('data_quality_findings')}",
    )
    ar = api.get("/areas", headers=auth, params={"dataset_version_id": up["dataset_version_id"], "level": "lga"})
    its = ar.json().get("items", []) if ar.status_code == 200 else []
    ind = expected_analysis["indicators"]
    check(
        "Plateau LGA totals agree with the analysis",
        sum(i["units"] for i in its) == 4989
        and sum(i["turnout"]["units"] for i in its) == ind["turnout_rate"]["eligible"]
        and sum(i["vote_share"]["units"] for i in its) == ind["leading_party_share"]["eligible"]
        and all(i["in_reference_list"] for i in its),
        f"HTTP {ar.status_code}; {len(its)} LGAs",
    )
    return L.report()


if __name__ == "__main__":
    sys.exit(main())
