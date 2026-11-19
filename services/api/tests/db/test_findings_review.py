"""Phase 5: review decisions, claims and verification, the finding_overview view, append-only history."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import errors

from .conftest import as_service, as_user
from .test_ingestion import H


def review(api, world, who, reason="Checked the result sheet image.", decision="acknowledged", org="a"):
    o = getattr(world, org)
    return api.post(
        f"/findings/{world.a.rows['findings']}/reviews",
        json={"decision": decision, "reason": reason},
        headers=H(o.users[who], str(uuid.uuid4())),
    )


def new_claim(api, world, who="researcher", evidence=None, finding=True):
    body = {"project_id": world.a.rows["projects"], "statement": "Observers reported the count started late."}
    if finding:
        body["finding_id"] = world.a.rows["findings"]
    if evidence is not None:
        body["evidence"] = evidence
    return api.post("/claims", json=body, headers=H(world.a.users[who], str(uuid.uuid4())))


LINK = [{"kind": "link", "description": "Observer group statement", "url": "https://example.org/statement"}]


# ------------------------------------------------------------------ reviews


@pytest.mark.parametrize("who,status", [("admin", 201), ("analyst", 201), ("researcher", 403), ("viewer", 403)])
def test_review_permissions_follow_the_table(api, world, who, status):
    assert review(api, world, who).status_code == status


def test_review_from_other_org_is_404_and_reason_is_required(api, world):
    assert review(api, world, "admin", org="b").status_code == 404
    r = review(api, world, "admin", reason="          short")
    assert r.status_code == 400
    assert review(api, world, "admin", decision="fraud").status_code == 400


def test_review_is_idempotent(api, world):
    h = H(world.a.users["analyst"], "same-key-review")
    body = {"decision": "escalated", "reason": "Needs a field visit to confirm."}
    url = f"/findings/{world.a.rows['findings']}/reviews"
    a, b = api.post(url, json=body, headers=h), api.post(url, json=body, headers=h)
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]


def test_latest_review_is_the_current_status_and_history_is_kept(api, world, conn):
    first = review(api, world, "analyst", decision="needs_more_evidence").json()
    last = review(api, world, "admin", decision="explained_by_data_error", reason="Sheet shows 112, not 412.").json()
    with as_user(conn, world.a.users["viewer"]) as c:
        row = c.execute(
            "SELECT review_status, note_count, claim_count FROM public.finding_overview WHERE id=%s",
            (world.a.rows["findings"],),
        ).fetchone()
        ids = {r["id"] for r in c.execute(
            "SELECT id::text FROM public.finding_reviews WHERE finding_id=%s", (world.a.rows["findings"],)
        ).fetchall()}  # fmt: skip
    assert row["review_status"] == "explained_by_data_error"
    assert row["note_count"] >= 1 and row["claim_count"] >= 1
    assert {first["id"], last["id"]} <= ids


@pytest.mark.parametrize("sql", [
    "UPDATE public.finding_reviews SET decision='acknowledged' WHERE id=%s",
    "DELETE FROM public.finding_reviews WHERE id=%s",
])  # fmt: skip
def test_review_history_cannot_be_altered_even_by_the_database_owner(world, sql):
    rid = world.a.rows["finding_reviews"]
    with psycopg.connect(world.dsn) as c, pytest.raises(errors.InsufficientPrivilege):
        c.execute(sql, (rid,))  # table owner, no RLS: the trigger still refuses
    with psycopg.connect(world.dsn) as c, pytest.raises(errors.InsufficientPrivilege):
        c.execute("TRUNCATE public.finding_reviews")


def test_review_history_cannot_be_altered_by_api_roles(world, conn):
    rid = world.a.rows["finding_reviews"]
    for who in ("admin", "analyst"):
        with pytest.raises((errors.InsufficientPrivilege, errors.RaiseException)):
            with as_user(conn, world.a.users[who]) as c:
                c.execute("UPDATE public.finding_reviews SET reason='Changed my mind later.' WHERE id=%s", (rid,))
                if c.rowcount == 0:
                    raise errors.InsufficientPrivilege("no rows updatable")
    with pytest.raises(errors.InsufficientPrivilege):
        with as_service(conn) as c:
            c.execute("DELETE FROM public.finding_reviews WHERE id=%s", (rid,))


def test_review_is_audited_with_its_author(api, world, conn):
    r = review(api, world, "analyst", decision="acknowledged", reason="Audit trail check for this review.").json()
    ev = conn.execute(
        "SELECT actor_id::text AS actor, action, details->>'finding_id' AS fid FROM public.audit_events "
        "WHERE entity_id=%s",
        (r["id"],),
    ).fetchone()
    assert ev["actor"] == world.a.users["analyst"] and ev["action"] == "finding_reviews.insert"
    assert ev["fid"] == world.a.rows["findings"]


# ------------------------------------------------------------------ notes


def test_notes_permissions(world, conn):
    body = "Called the ward collation officer."
    with as_user(conn, world.a.users["researcher"]) as c:
        c.execute(
            "INSERT INTO public.investigation_notes (org_id, finding_id, body, author_id) VALUES (%s,%s,%s,%s)",
            (world.a.id, world.a.rows["findings"], body, world.a.users["researcher"]),
        )
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(conn, world.a.users["viewer"]) as c:
            c.execute(
                "INSERT INTO public.investigation_notes (org_id, finding_id, body, author_id) VALUES (%s,%s,%s,%s)",
                (world.a.id, world.a.rows["findings"], body, world.a.users["viewer"]),
            )


# ------------------------------------------------------------------ claims


@pytest.mark.parametrize("who,status", [("admin", 201), ("analyst", 201), ("researcher", 201), ("viewer", 403)])
def test_claim_permissions(api, world, who, status):
    r = new_claim(api, world, who)
    assert r.status_code == status, r.text
    if status == 201:
        assert r.json()["status"] == "unverified_allegation" and r.json()["evidence_count"] == 0


def test_claim_cannot_attach_a_finding_from_another_project(api, world):
    body = {"project_id": world.a.rows["projects"], "finding_id": world.b.rows["findings"],
            "statement": "Cross-project attachment attempt."}  # fmt: skip
    assert api.post("/claims", json=body, headers=H(world.a.users["analyst"])).status_code == 404
    other = {"project_id": world.b.rows["projects"], "statement": "Claim in someone else's project."}
    assert api.post("/claims", json=other, headers=H(world.a.users["admin"])).status_code == 404


def test_verification_rules(api, world):
    no_ev = new_claim(api, world, "researcher").json()
    with_ev = new_claim(api, world, "researcher", evidence=LINK).json()
    assert with_ev["evidence_count"] == 1
    v = lambda cid, who, reason="Matched against the observer report.": api.post(  # noqa: E731
        f"/claims/{cid}/verify", json={"reason": reason}, headers=H(world.a.users[who], str(uuid.uuid4()))
    )
    assert v(no_ev["id"], "admin").status_code == 409, "no evidence"
    assert v(with_ev["id"], "researcher").status_code == 403, "researchers cannot verify"
    assert v(with_ev["id"], "viewer").status_code == 403
    mine = new_claim(api, world, "analyst", evidence=LINK).json()
    r = v(mine["id"], "analyst")
    assert r.status_code == 409 and "someone other" in r.json()["message"]
    ok = v(with_ev["id"], "analyst")
    assert ok.status_code == 200 and ok.json()["status"] == "verified_fact"
    assert ok.json()["verified_by"] == world.a.users["analyst"]
    assert v(with_ev["id"], "admin").status_code == 409, "already verified"


def test_database_refuses_verification_without_evidence(api, world, conn):
    cid = new_claim(api, world, "researcher").json()["id"]
    with pytest.raises(errors.CheckViolation):
        with as_service(conn) as c:
            c.execute(
                "UPDATE public.claims SET status='verified_fact', verified_by=%s, verified_at=now(), "
                "verification_reason='Looks right to me.' WHERE id=%s",
                (world.a.users["admin"], cid),
            )


def test_claim_statement_is_immutable(api, world, conn):
    cid = new_claim(api, world, "researcher").json()["id"]
    with pytest.raises(errors.InsufficientPrivilege):
        with as_service(conn) as c:
            c.execute("UPDATE public.claims SET statement='Edited after the fact.' WHERE id=%s", (cid,))


def test_bad_evidence_is_rejected(api, world):
    bad = [{"kind": "link", "description": "x", "url": None}]
    assert new_claim(api, world, evidence=bad).status_code == 400
    bad = [{"kind": "file", "description": "sheet photo", "file_sha256": "nothex"}]
    assert new_claim(api, world, evidence=bad).status_code == 400


# ------------------------------------------------------------------ the view respects RLS and MFA


def test_finding_overview_is_tenant_scoped_and_needs_two_step(world, conn):
    with as_user(conn, world.b.users["admin"]) as c:
        assert c.execute(
            "SELECT count(*) AS n FROM public.finding_overview WHERE org_id=%s", (world.a.id,)
        ).fetchone()["n"] == 0  # fmt: skip
    with as_user(conn, world.a.users["viewer"], aal="aal1") as c:
        assert c.execute("SELECT count(*) AS n FROM public.finding_overview").fetchone()["n"] == 0
    with as_user(conn, world.a.users["viewer"]) as c:
        assert c.execute("SELECT count(*) AS n FROM public.finding_overview").fetchone()["n"] >= 1


# ------------------------------------------------------------------ GET /findings and GET /findings/{id}

from .test_analysis_job import _analyse, _validate  # noqa: E402
from .test_ingestion import drain  # noqa: E402
from .test_validation_job import ingested  # noqa: E402, F401


def test_list_filter_paginate_and_inspect(api, world, ingested):  # noqa: F811
    _validate(api, world, ingested)
    assert _analyse(api, world, ingested).status_code == 202
    drain(world)
    pid, hv = world.a.rows["projects"], H(world.a.users["viewer"])

    page = api.get("/findings", params={"project_id": pid, "limit": 200}, headers=hv).json()
    seen, items = set(), []
    while True:
        items += page["items"]
        if not page["next_cursor"]:
            break
        page = api.get("/findings", params={"project_id": pid, "limit": 200, "cursor": page["next_cursor"]},
                       headers=hv).json()  # fmt: skip
    seen = {i["id"] for i in items}
    assert len(items) == len(seen) == 140 + 79, "cursor pages cover every finding of the latest run exactly once"
    ranks = [{"very_high": 5, "high": 4, "medium": 3, "low": 2}[i["severity"]] for i in items]
    assert ranks == sorted(ranks, reverse=True)

    vh = api.get("/findings", params={"project_id": pid, "category": "statistical_anomaly", "severity": "very_high",
                                      "indicator": "turnout_rate"}, headers=hv).json()["items"]  # fmt: skip
    assert len(vh) == 15 and all(i["indicator"] == "turnout_rate" for i in vh)
    f = vh[0]
    assert f["statistics"]["robust_z"] and f["peer_group"]["sample_size"] >= 10 and f["review_status"] == "unreviewed"
    ev = f["evidence"][0]
    assert ev["cells"]["PU Code"] == f["pu_code"], "evidence shows the original cells of the source row"
    assert len(f["provenance"]["dataset_sha256"]) == 64

    # review → status filter
    api.post(f"/findings/{f['id']}/reviews", json={"decision": "escalated", "reason": "Sheet needs a field check."},
             headers=H(world.a.users["analyst"]))  # fmt: skip
    esc = api.get("/findings", params={"project_id": pid, "review_status": "escalated"}, headers=hv).json()["items"]
    assert [i["id"] for i in esc] == [f["id"]]

    dq = api.get("/findings", params={"project_id": pid, "category": "data_quality_issue", "limit": 200},
                 headers=hv).json()["items"]  # fmt: skip
    one = next(i for i in dq if i["rule_id"] == "V004")
    detail = api.get(f"/findings/{one['id']}", headers=hv).json()
    assert detail["evidence"][0]["flagged_columns"] and detail["statistics"] is None

    geo = api.get("/findings", params={"project_id": pid, "lga": f["lga"].upper(), "ward": f["ward"]},
                  headers=hv).json()["items"]  # fmt: skip
    assert geo and all(i["lga"] == f["lga"] and i["ward"] == f["ward"] for i in geo)


def test_findings_are_hidden_from_other_orgs(api, world):
    assert api.get(f"/findings/{world.a.rows['findings']}", headers=H(world.b.users["admin"])).status_code == 404
    r = api.get("/findings", params={"project_id": world.a.rows["projects"]}, headers=H(world.b.users["viewer"]))
    assert r.status_code == 404
    assert api.get("/findings", params={"project_id": world.a.rows["projects"], "cursor": "bad"},
                   headers=H(world.a.users["viewer"])).status_code == 400  # fmt: skip
