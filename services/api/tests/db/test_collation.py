"""Collation check end to end on the migrated test DB with the SYNTHETIC results and declared totals."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row

from app.collation.synthetic import build

from .test_analysis_job import _analyse, _validate
from .test_ingestion import H, drain
from .test_validation_job import ingested  # noqa: F401


@pytest.fixture()
def validated(api, world, ingested):  # noqa: F811
    _validate(api, world, ingested)
    return ingested


def run_check(api, world, vid, who="analyst", text=None, key=None):
    body = {"project_id": world.a.rows["projects"], "dataset_version_id": vid, "filename": "declared.csv",
            "csv_text": text if text is not None else build()}  # fmt: skip
    return api.post("/collation-checks", json=body, headers=H(world.a.users[who], key or str(uuid.uuid4())))


def q(world, sql, *args):
    with psycopg.connect(world.dsn, row_factory=dict_row) as c:
        return c.execute(sql, args).fetchall()


def test_planted_differences_are_found_and_become_findings(api, world, validated):
    key = str(uuid.uuid4())
    r = run_check(api, world, validated, key=key)
    assert r.status_code == 201, r.text
    c = r.json()
    areas = {(a["level"], a["ward"] or a["lga"] or a["state"]): a for a in c["areas"]}
    assert c["summary"]["areas"] == 12 and c["summary"]["parties"] == ["PA", "PB", "PC", "PD", "PE"]
    assert c["summary"]["ignored_columns"] == ["Level", "Source"] or "Source" in c["summary"]["ignored_columns"]

    w2 = areas[("ward", "Amber Ward 2")]
    assert w2["status"] == "mismatch"
    pa = next(x for x in w2["figures"] if x["field"] == "party:PA")
    assert pa["diff"] == 120 and pa["match"] is False
    w3 = areas[("ward", "Amber Ward 3")]
    assert next(x for x in w3["figures"] if x["field"] == "valid_votes")["diff"] == -40
    lga = areas[("lga", "Amber North")]
    assert next(x for x in lga["figures"] if x["field"] == "total_cast")["diff"] == 35
    # Every other area either matches exactly or could not be checked for some figure (blank cells in the data).
    others = [a for k, a in areas.items() if k not in {("ward", "Amber Ward 2"), ("ward", "Amber Ward 3"),
                                                     ("lga", "Amber North")}]  # fmt: skip
    for a in others:
        assert a["status"] in ("match", "incomplete"), (a["ward"], a["status"], a["figures"])
        assert all(x["match"] is not False for x in a["figures"])
    assert c["summary"]["mismatch"] == 3 and len(c["finding_ids"]) == 3

    f = api.get(f"/findings/{w2['finding_id']}", headers=H(world.a.users["viewer"])).json()
    assert f["category"] == "cross_check" and f["rule_id"] == "collation_ward" and f["ward"] == "Amber Ward 2"
    assert f["severity"] == "high" and "PA votes: declared" in f["explanation"] and "(+120)" in f["explanation"]
    assert "cancelled or excluded at collation" in f["limitations"] and f["pu_code"] is None
    lf = api.get(f"/findings/{lga['finding_id']}", headers=H(world.a.users["viewer"])).json()
    assert lf["rule_id"] == "collation_lga" and lf["severity"] == "medium"  # 35 votes on 115k is under 1%

    again = run_check(api, world, validated, key=key)
    assert again.status_code == 201 and again.json()["id"] == c["id"], "idempotent replay"
    lst = api.get(
        "/collation-checks", params={"project_id": world.a.rows["projects"]}, headers=H(world.a.users["viewer"])
    )
    assert c["id"] in [x["id"] for x in lst.json()["items"]]
    one = api.get(f"/collation-checks/{c['id']}", headers=H(world.a.users["researcher"]))
    assert one.status_code == 200 and one.json()["sha256"] == c["sha256"]
    ev = q(world, "SELECT action FROM public.audit_events WHERE entity_id = %s", c["id"])
    assert [e["action"] for e in ev] == ["collation_checks.insert"]


def test_collation_findings_listed_with_the_run_and_in_reports(api, world, validated):
    from .test_reports import make

    assert _analyse(api, world, validated).status_code == 202
    drain(world)
    run = q(world, "SELECT latest_analysis_run_id::text AS r FROM public.dataset_versions WHERE id=%s", validated)[0][
        "r"
    ]
    c = run_check(api, world, validated).json()
    page = api.get("/findings", params={"project_id": world.a.rows["projects"], "analytical_run_id": run,
                                        "category": "cross_check"}, headers=H(world.a.users["viewer"])).json()  # fmt: skip
    assert set(c["finding_ids"]) <= {x["id"] for x in page["items"]}
    md = api.get(f"/reports/{make(api, world, run).json()['id']}/export", params={"format": "md"},
                 headers=H(world.a.users["analyst"])).text  # fmt: skip
    assert "Declared ward total differs from the sum of its polling units" in md


def test_permissions_bad_files_and_immutability(api, world, validated, conn):
    for who, code in (("researcher", 403), ("viewer", 403)):
        assert run_check(api, world, validated, who=who).status_code == code
    other = {"project_id": world.b.rows["projects"], "dataset_version_id": validated, "filename": "x.csv",
             "csv_text": build()}  # fmt: skip
    assert api.post("/collation-checks", json=other, headers=H(world.b.users["admin"])).status_code == 404
    bad = run_check(api, world, validated, text="LGA,Ward,Valid\nX,Y,1\n")
    assert bad.status_code == 400 and "State column" in bad.json()["message"]
    unvalidated = {"project_id": world.a.rows["projects"], "dataset_version_id": world.a.rows["dataset_versions"],
                   "filename": "x.csv", "csv_text": build()}  # fmt: skip
    assert api.post("/collation-checks", json=unvalidated, headers=H(world.a.users["admin"])).status_code == 409
    c = run_check(api, world, validated).json()
    assert api.get(f"/collation-checks/{c['id']}", headers=H(world.b.users["admin"])).status_code == 404
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute("UPDATE public.collation_checks SET summary = '{}' WHERE id = %s", (c["id"],))
    conn.rollback()
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute("DELETE FROM public.collation_checks WHERE id = %s", (c["id"],))
