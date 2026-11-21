"""Phase 16: field reports from polling units, on the migrated test DB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row

from app.sheets.synthetic import figures_from_csv, render

from .conftest import as_service, as_user
from .test_escalations import inbox
from .test_hierarchy import assign, member
from .test_ingestion import H
from .test_sheets import ai_reply, b64, vision  # noqa: F401


@pytest.fixture(scope="module")
def field(world):
    agent, other, coord = member(world, "field_agent"), member(world, "field_agent"), member(world, "coordinator")
    assign(world, agent, "polling_unit", "S5", "L5", "W5", "SY-01-01-01-00002")
    assign(world, other, "polling_unit", "S5", "L5", "W5", "SY-50")
    assign(world, coord, "ward", "S5", "L5", "W5")
    return {"agent": agent, "other": other, "coord": coord}


def now(minutes: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def send(api, world, uid, kind, payload, pu="SY-01-01-01-00002", client_id=None, captured_at=None):
    body = {"project_id": world.a.rows["projects"], "client_id": client_id or uuid.uuid4().hex, "kind": kind,
            "pu_code": pu, "captured_at": captured_at or now(), "payload": payload,
            "location": {"lat": 9.92, "lng": 8.89, "accuracy_m": 12}}  # fmt: skip
    return api.post("/field-reports", json=body, headers=H(uid, str(uuid.uuid4())))


ARRIVAL = {"opened": True, "opened_at": "08:40", "officials_present": 4, "bvas_working": True,
           "materials_complete": True, "security_present": True}  # fmt: skip


def test_agent_reports_from_their_unit_and_resends_are_harmless(api, world, field):
    cid = uuid.uuid4().hex
    r = send(api, world, field["agent"], "arrival", ARRIVAL, client_id=cid)
    assert r.status_code == 201, r.text
    rep = r.json()
    assert (rep["state"], rep["lga"], rep["ward"]) == ("S5", "L5", "W5") and rep["location"]["accuracy_m"] == 12
    again = send(api, world, field["agent"], "arrival", ARRIVAL, client_id=cid)  # signal dropped, phone resends
    assert again.status_code == 200 and again.json()["id"] == rep["id"]
    assert send(api, world, field["other"], "arrival", ARRIVAL, client_id=cid).status_code == 409
    # Codes are matched without regard to case or spacing.
    acc = send(api, world, field["agent"], "accreditation", {"accredited_on_bvas": 212, "bvas_working": True},
               pu=" sy-01-01-01-00002 ")  # fmt: skip
    assert acc.status_code == 201 and acc.json()["pu_code"] == "SY-01-01-01-00002"


def test_agents_cannot_report_for_other_units_and_bad_reports_are_refused(api, world, field):
    assert send(api, world, field["agent"], "arrival", ARRIVAL, pu="SY-50").status_code == 403
    assert send(api, world, world.a.users["viewer"], "arrival", ARRIVAL).status_code == 403
    bad = [
        ("accreditation", {"accredited_on_bvas": -3, "bvas_working": True}),
        ("accreditation", {"accredited_on_bvas": 10, "bvas_working": True, "extra": 1}),
        ("arrival", {**ARRIVAL, "opened_at": "8.40am"}),
        ("incident", {"category": "riot", "severity": "low", "description": "Crowd", "ongoing": False}),
        ("incident", {"category": "violence", "severity": "critical", "description": "  ", "ongoing": True}),
        ("closing", {"counting_done": True}),
    ]
    for kind, payload in bad:
        r = send(api, world, field["agent"], kind, payload)
        assert r.status_code == 400, (kind, payload, r.text)
    assert send(api, world, field["agent"], "arrival", ARRIVAL, captured_at=now(60)).status_code == 400
    assert send(api, world, field["agent"], "arrival", ARRIVAL, captured_at=now(-60 * 24 * 8)).status_code == 400
    # Admins may report for a unit the organisation knows; not for an unknown one.
    assert send(api, world, world.a.users["admin"], "arrival", ARRIVAL, pu="SY-50").status_code == 201
    assert send(api, world, world.a.users["admin"], "arrival", ARRIVAL, pu="NOPE-1").status_code == 400


def test_critical_incident_goes_straight_to_the_ward_coordinator(api, world, field):
    r = send(api, world, field["agent"], "incident", {"category": "ballot_snatching", "severity": "critical",
             "description": "Two men took the ballot box at 13:10.", "ongoing": False})  # fmt: skip
    assert r.status_code == 201, r.text
    eid = r.json()["escalation_id"]
    assert eid
    e = api.get(f"/escalations/{eid}", headers=H(field["coord"])).json()
    assert e["current_level"] == "ward" and e["title"] == "Ballot box snatched at SY-01-01-01-00002"
    assert e["source_kind"] == "field_report" and e["can_resolve"]
    assert f"/escalations/{eid}" in [n["link"] for n in inbox(world, field["coord"])]
    low = send(api, world, field["agent"], "incident", {"category": "late_opening", "severity": "low",
               "description": "Opened at 09:15.", "ongoing": False})  # fmt: skip
    assert low.status_code == 201 and low.json()["escalation_id"] is None


def test_agents_see_only_their_own_reports(api, world, field):
    send(api, world, field["other"], "arrival", ARRIVAL, pu="SY-50")
    params = {"project_id": world.a.rows["projects"]}
    mine = api.get("/field-reports", params=params, headers=H(field["agent"])).json()["items"]
    assert mine and all(x["agent_id"] == field["agent"] for x in mine)
    everyone = api.get("/field-reports", params=params, headers=H(world.a.users["analyst"])).json()["items"]
    assert {field["agent"], field["other"]} <= {x["agent_id"] for x in everyone}
    with psycopg.connect(world.dsn, row_factory=dict_row, autocommit=True) as c:
        with as_user(c, field["agent"]) as u:
            ids = {r["agent_id"] for r in u.execute("SELECT agent_id::text FROM public.field_reports").fetchall()}
            assert ids == {field["agent"]}
        with as_user(c, world.b.users["admin"]) as u:
            assert u.execute("SELECT count(*) AS n FROM public.field_reports").fetchone()["n"] == 0
        with pytest.raises(errors.Error), as_service(c, world.a.users["admin"]) as s:
            s.execute("UPDATE public.field_reports SET payload = '{}' WHERE agent_id = %s", (field["agent"],))
    assert api.get("/field/assignments", params=params, headers=H(field["agent"])).json()["items"] == [
        {"pu_code": "SY-01-01-01-00002", "state": "S5", "lga": "L5", "ward": "W5"}]  # fmt: skip


def test_result_sheet_on_election_day_with_no_results_file(api, world, field, vision):  # noqa: F811
    fig = figures_from_csv(2)
    fig["party_votes"][0]["votes"] += 15  # party votes no longer add up to valid votes
    vision(ai_reply(fig))
    body = {"project_id": world.a.rows["projects"], "image_base64": b64(render(fig))}
    up = api.post("/sheets", json=body, headers=H(field["agent"], str(uuid.uuid4())))
    assert up.status_code == 201, up.text
    sheet = up.json()
    assert sheet["dataset_version_id"] is None
    # Before confirming, it can't be sent as a result; other agents can't see the sheet at all.
    assert send(api, world, field["agent"], "result", {"sheet_id": sheet["id"]}).status_code == 409
    assert api.get(f"/sheets/{sheet['id']}", headers=H(field["other"])).status_code == 404
    figures = {**{k: v["value"] for k, v in ai_reply(fig).items() if isinstance(v, dict)},
               "party_votes": fig["party_votes"], "state": "S5", "lga": "L5", "ward": "W5"}  # fmt: skip
    ok = api.post(f"/sheets/{sheet['id']}/confirm", json={"figures": figures},
                  headers=H(field["agent"], str(uuid.uuid4())))  # fmt: skip
    assert ok.status_code == 200, ok.text
    c = ok.json()
    assert {x["id"]: x["ok"] for x in c["checks"]}["sheet_party_sum"] is False
    assert {x["id"]: x["ok"] for x in c["checks"]}["sheet_vs_data"] is None
    assert c["finding_ids"] == [] and c["escalation_id"]
    e = api.get(f"/escalations/{c['escalation_id']}", headers=H(field["coord"])).json()
    assert e["source_kind"] == "result_sheet" and e["current_level"] == "ward"
    res = send(api, world, field["agent"], "result", {"sheet_id": sheet["id"]})
    assert res.status_code == 201 and res.json()["sheet_id"] == sheet["id"]
    assert send(api, world, field["other"], "result", {"sheet_id": sheet["id"]}, pu="SY-50").status_code == 404
