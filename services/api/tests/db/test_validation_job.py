"""POST /jobs (validate) → worker → result_records + validation_issues, on the migrated test DB."""

from __future__ import annotations

import csv

import psycopg
import pytest
from psycopg.rows import dict_row

from .test_ingestion import DEMO, GOOD_MAPPING, REPO, H, drain, upload_and_finalize

MAPPING = {
    **GOOD_MAPPING,
    "fields": {**GOOD_MAPPING["fields"], "latitude": "Latitude", "longitude": "Longitude"},
}


def truth():
    with (REPO / "data/synthetic/SYNTHETIC_ground_truth_seed2026.csv").open() as f:
        return list(csv.DictReader(f))


def load_units(world, project_id):
    with (REPO / "data/synthetic/SYNTHETIC_admin_units_seed2026.csv").open() as f:
        units = [(r["state"], r["lga"], r["ward"]) for r in csv.DictReader(f)]
    with psycopg.connect(world.dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.executemany(
                "INSERT INTO public.admin_units (org_id, project_id, state, lga, ward, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                [(world.a.id, project_id, *u, world.a.users["analyst"]) for u in units],
            )


@pytest.fixture()
def ingested(api, world):
    up, _ = upload_and_finalize(api, world, DEMO)
    drain(world)
    r = api.put(
        f"/dataset-versions/{up['dataset_version_id']}/mapping", json=MAPPING, headers=H(world.a.users["analyst"])
    )
    assert r.json()["issues"] == []
    return up["dataset_version_id"]


def q(world, sql, *args):
    with psycopg.connect(world.dsn, row_factory=dict_row) as c:
        return c.execute(sql, args).fetchall()


def test_validation_end_to_end_matches_ground_truth(api, world, ingested):
    load_units(world, world.a.rows["projects"])
    r = api.post(
        "/jobs", json={"job_type": "validate", "dataset_version_id": ingested}, headers=H(world.a.users["researcher"])
    )
    assert r.status_code == 202, r.text
    job = r.json()
    again = api.post(
        "/jobs", json={"job_type": "validate", "dataset_version_id": ingested}, headers=H(world.a.users["analyst"])
    )
    assert again.status_code == 200 and again.json()["id"] == job["id"], "duplicate request returns the live job"
    drain(world)
    assert api.get(f"/jobs/{job['id']}", headers=H(world.a.users["viewer"])).json()["status"] == "succeeded"

    assert (
        q(world, "SELECT count(*) AS n FROM public.result_records WHERE dataset_version_id=%s", ingested)[0]["n"]
        == 12_023
    )
    issues = q(
        world,
        "SELECT i.rule_id, i.source_row_number, i.source_column, i.original_value, r.cells, r.source_row_number AS rn "
        "FROM public.validation_issues i JOIN public.raw_rows r ON r.id = i.raw_row_id WHERE i.dataset_version_id=%s",
        ingested,
    )
    expected = {
        (int(t["source_row_number"]), rule) for t in truth() for rule in filter(None, t["expected_rules"].split("|"))
    }
    assert {(i["source_row_number"], i["rule_id"]) for i in issues} == expected
    assert all(i["rn"] == i["source_row_number"] for i in issues), "every issue links to its raw source row"

    v = q(
        world, "SELECT validation_summary, validation_job_id::text FROM public.dataset_versions WHERE id=%s", ingested
    )[0]
    s = v["validation_summary"]
    assert v["validation_job_id"] == job["id"]
    assert s["rows"] == 12_023 and s["reference_list_used"] is True and s["ruleset_version"] == "1.1.0"
    assert s["row_status"]["eligible"] == sum(1 for t in truth() if not t["expected_rules"])
    rules = {r["rule_id"] for r in q(world, "SELECT rule_id FROM public.validation_rules")}
    assert rules == {f"V{n:03d}" for n in range(1, 13)}


def test_revalidation_replaces_results_without_duplicates(api, world, ingested):
    for _ in range(2):
        r = api.post(
            "/jobs", json={"job_type": "validate", "dataset_version_id": ingested}, headers=H(world.a.users["analyst"])
        )
        assert r.status_code in (200, 202)
        drain(world)
        # change the input so the next request is a new job (different mapping fingerprint)
        m = {**MAPPING, "fields": {k: v for k, v in MAPPING["fields"].items() if k not in ("latitude", "longitude")}}
        api.put(f"/dataset-versions/{ingested}/mapping", json=m, headers=H(world.a.users["analyst"]))
    n = q(world, "SELECT count(*) AS n FROM public.result_records WHERE dataset_version_id=%s", ingested)[0]["n"]
    assert n == 12_023
    v12 = q(
        world,
        "SELECT count(*) AS n FROM public.validation_issues WHERE dataset_version_id=%s AND rule_id='V012'",
        ingested,
    )
    assert v12[0]["n"] == 0, "latest run (no coordinates mapped) replaced the earlier issues"


@pytest.mark.parametrize("role,status", [("viewer", 403), ("researcher", 202)])
def test_roles(api, world, ingested, role, status):
    r = api.post("/jobs", json={"job_type": "validate", "dataset_version_id": ingested}, headers=H(world.a.users[role]))
    assert r.status_code in ((status, 200) if status == 202 else (status,))


def test_other_org_and_bad_requests(api, world, ingested):
    body = {"job_type": "validate", "dataset_version_id": ingested}
    assert api.post("/jobs", json=body, headers=H(world.b.users["admin"])).status_code == 404
    assert (
        api.post("/jobs", json={**body, "job_type": "analyse"}, headers=H(world.a.users["analyst"])).status_code == 409
    )
    assert (
        api.post("/jobs", json={**body, "job_type": "ingest"}, headers=H(world.a.users["analyst"])).status_code == 400
    )


def test_requires_complete_mapping(api, world):
    up, _ = upload_and_finalize(api, world, DEMO)
    drain(world)
    body = {"job_type": "validate", "dataset_version_id": up["dataset_version_id"]}
    r = api.post("/jobs", json=body, headers=H(world.a.users["analyst"]))
    assert r.status_code == 409 and "Map the columns" in r.json()["message"]
    bad = {**MAPPING, "fields": {k: v for k, v in MAPPING["fields"].items() if k != "ward"}}
    api.put(f"/dataset-versions/{up['dataset_version_id']}/mapping", json=bad, headers=H(world.a.users["analyst"]))
    r = api.post("/jobs", json=body, headers=H(world.a.users["analyst"]))
    assert r.status_code == 409 and "incomplete" in r.json()["message"]
