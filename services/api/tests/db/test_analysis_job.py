"""POST /jobs (analyse) → worker → analytical run + findings, on the migrated test DB."""

from __future__ import annotations

import psycopg
import pytest

from .test_ingestion import H, drain
from .test_validation_job import ingested, load_units, q  # noqa: F401  (fixture)


def _validate(api, world, version_id):
    load_units(world, world.a.rows["projects"])
    r = api.post(
        "/jobs", json={"job_type": "validate", "dataset_version_id": version_id}, headers=H(world.a.users["analyst"])
    )
    assert r.status_code == 202, r.text
    drain(world)


def _analyse(api, world, version_id, who="researcher"):
    return api.post(
        "/jobs", json={"job_type": "analyse", "dataset_version_id": version_id}, headers=H(world.a.users[who])
    )


def test_analysis_needs_validation_first(api, world, ingested):  # noqa: F811
    r = _analyse(api, world, ingested)
    assert r.status_code == 409 and "validation" in r.json()["message"].lower()


def test_analysis_end_to_end(api, world, ingested):  # noqa: F811
    _validate(api, world, ingested)
    r = _analyse(api, world, ingested)
    assert r.status_code == 202, r.text
    job = r.json()
    dup = _analyse(api, world, ingested, "analyst")
    assert dup.status_code == 200 and dup.json()["id"] == job["id"]
    drain(world)
    view = api.get(f"/jobs/{job['id']}", headers=H(world.a.users["viewer"])).json()
    assert view["status"] == "succeeded" and view["analytical_run_id"]

    run = q(world, "SELECT * FROM public.analytical_runs WHERE id=%s", view["analytical_run_id"])[0]
    assert run["status"] == "complete" and run["method_version"] == "1.0.0"
    s = run["summary"]
    assert s["statistical_findings"] == 140 and s["data_quality_findings"] == 79
    assert s["indicators"]["turnout_rate"]["not_scored"] == {"excluded_by_validation": 33, "insufficient_peers": 8,
                                                             "zero_mad": 12}  # fmt: skip
    n = q(world, "SELECT category, count(*) AS n FROM public.findings WHERE analytical_run_id=%s GROUP BY 1", run["id"])
    assert {r["category"]: r["n"] for r in n} == {"statistical_anomaly": 140, "data_quality_issue": 79}
    v = q(world, "SELECT latest_analysis_run_id::text AS r FROM public.dataset_versions WHERE id=%s", ingested)[0]
    assert v["r"] == view["analytical_run_id"]

    # every statistical finding links to its raw source row
    bad = q(
        world,
        "SELECT count(*) AS n FROM public.findings f JOIN public.raw_rows r ON r.id = f.raw_row_id "
        "WHERE f.analytical_run_id=%s AND (f.evidence->0->>'source_row_number')::int <> r.source_row_number",
        run["id"],
    )
    assert bad[0]["n"] == 0

    # findings are immutable
    with psycopg.connect(world.dsn) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("UPDATE public.findings SET severity='low' WHERE analytical_run_id=%s", (run["id"],))

    # same input → a second run gives the same content hash (a finished job is not reused)
    r2 = _analyse(api, world, ingested)
    assert r2.status_code == 202 and r2.json()["id"] != job["id"]
    drain(world)
    runs = q(world, "SELECT content_hash FROM public.analytical_runs WHERE dataset_version_id=%s", ingested)
    assert len(runs) == 2 and runs[0]["content_hash"] == runs[1]["content_hash"]


def test_viewer_cannot_start_analysis_and_other_org_gets_404(api, world, ingested):  # noqa: F811
    _validate(api, world, ingested)
    assert _analyse(api, world, ingested, "viewer").status_code == 403
    other = api.post("/jobs", json={"job_type": "analyse", "dataset_version_id": ingested},
                     headers=H(world.b.users["admin"]))  # fmt: skip
    assert other.status_code == 404
