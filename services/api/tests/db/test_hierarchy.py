"""Phase 15: coordinators and field agents; escalations climb ward -> LGA -> state -> admins."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row

from .conftest import as_service, as_user
from .test_escalations import inbox, new_finding
from .test_ingestion import H


def member(world, role: str) -> str:
    with psycopg.connect(world.dsn, row_factory=dict_row, autocommit=True) as c:
        uid = str(c.execute("INSERT INTO auth.users (email) VALUES (%s) RETURNING id",
                            (f"{role}.{uuid.uuid4().hex[:8]}@example.test",)).fetchone()["id"])  # fmt: skip
        with as_service(c, world.a.users["admin"]) as s:
            s.execute("INSERT INTO public.memberships (org_id, user_id, role, created_by) VALUES (%s,%s,%s,%s)",
                      (world.a.id, uid, role, world.a.users["admin"]))  # fmt: skip
    return uid


def assign(world, uid: str, level: str, state="S1", lga=None, ward=None, pu=None, who=None) -> None:
    with psycopg.connect(world.dsn, row_factory=dict_row, autocommit=True) as c:
        admin = who or world.a.users["admin"]
        with as_user(c, admin, commit=True) as u:
            u.execute(
                """INSERT INTO public.area_assignments (org_id, user_id, level, state, lga, ward, pu_code, created_by)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (world.a.id, uid, level, state, lga, ward, pu, admin),
            )


@pytest.fixture(scope="module")
def chain(world):
    ward, lga, state = member(world, "coordinator"), member(world, "coordinator"), member(world, "coordinator")
    assign(world, ward, "ward", "S1", "L1", "W1")
    assign(world, lga, "lga", "s1", "l1")  # names match whatever the case and spacing
    assign(world, state, "state", "S1 ")
    return {"ward": ward, "lga": lga, "state": state}


def act(api, uid, eid, kind, note="Checked locally; passing it on."):
    return api.post(f"/escalations/{eid}/{kind}", json={"note": note}, headers=H(uid, str(uuid.uuid4())))


def test_escalation_climbs_the_chain_one_level_at_a_time(api, world, chain):
    fid = new_finding(world, title="Unusual turnout at SY-20", area=("S1", "L1", "W1", "SY-20"))
    r = api.post(f"/findings/{fid}/reviews", json={"decision": "escalated", "reason": "Turnout 97%, please check."},
                 headers=H(world.a.users["analyst"], str(uuid.uuid4())))  # fmt: skip
    eid = r.json()["escalation_id"]
    link = f"/escalations/{eid}"
    e = api.get(link, headers=H(chain["ward"])).json()
    assert e["current_level"] == "ward" and e["can_resolve"] and e["can_send_up"]
    assert [n["link"] for n in inbox(world, chain["ward"])].count(link) == 1
    assert link not in [n["link"] for n in inbox(world, chain["lga"])]
    assert link not in [n["link"] for n in inbox(world, world.a.users["admin"])]
    # Admins can always step in and resolve, but they do not "send up".
    adm = api.get(link, headers=H(world.a.users["admin"])).json()
    assert adm["can_resolve"] and not adm["can_send_up"]
    # The LGA coordinator cannot act while it is with the ward.
    assert act(api, chain["lga"], eid, "send-up").status_code == 403

    assert act(api, chain["ward"], eid, "send-up").json()["current_level"] == "lga"
    assert act(api, chain["ward"], eid, "resolve").status_code == 403  # no longer theirs
    assert link in [n["link"] for n in inbox(world, chain["lga"])]
    assert act(api, chain["lga"], eid, "send-up").json()["current_level"] == "state"
    assert act(api, chain["state"], eid, "send-up").json()["current_level"] == "admin"
    assert link in [n["link"] for n in inbox(world, world.a.users["admin"])]
    assert act(api, chain["state"], eid, "send-up").status_code == 409  # admins are the top
    done = act(api, world.a.users["admin"], eid, "resolve", "Collation officer confirmed a recount.").json()
    assert done["status"] == "resolved"
    assert [x["kind"] for x in done["events"]] == ["raised", "sent_up", "sent_up", "sent_up", "resolved"]
    assert [x["level"] for x in done["events"]] == ["ward", "lga", "state", "admin", "admin"]


def test_routing_skips_levels_with_nobody_and_coordinator_can_resolve(api, world, chain):
    # Ward W2 has no coordinator, so it starts with the LGA coordinator, who resolves it.
    fid = new_finding(world, title="Unusual share at SY-21", area=("S1", "L1", "W2", "SY-21"))
    eid = api.post(f"/findings/{fid}/reviews", json={"decision": "escalated", "reason": "Share 99% for one party."},
                   headers=H(world.a.users["analyst"], str(uuid.uuid4()))).json()["escalation_id"]  # fmt: skip
    assert api.get(f"/escalations/{eid}", headers=H(chain["lga"])).json()["current_level"] == "lga"
    r = act(api, chain["lga"], eid, "resolve", "Spoke to the ward collation officer; explained.")
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    # A different state has no coordinators at all: straight to the admins.
    fid = new_finding(world, title="Unusual turnout at SY-22", area=("S9", "L9", "W9", "SY-22"))
    eid = api.post(f"/findings/{fid}/reviews", json={"decision": "escalated", "reason": "Turnout 99%, please check."},
                   headers=H(world.a.users["analyst"], str(uuid.uuid4()))).json()["escalation_id"]  # fmt: skip
    assert api.get(f"/escalations/{eid}", headers=H(chain["state"])).json()["current_level"] == "admin"


def test_coordinators_read_but_do_not_review(api, world, chain):
    fid = new_finding(world, title="Unusual turnout at SY-23", area=("S1", "L1", "W1", "SY-23"))
    assert api.get(f"/findings/{fid}", headers=H(chain["ward"])).status_code == 200
    r = api.post(f"/findings/{fid}/reviews", json={"decision": "acknowledged", "reason": "Coordinator decision."},
                 headers=H(chain["ward"], str(uuid.uuid4())))  # fmt: skip
    assert r.status_code == 403


def test_assignment_rules(api, world, chain):
    agent, viewer = member(world, "field_agent"), world.a.users["viewer"]
    assign(world, agent, "polling_unit", "S1", "L1", "W1", "SY-30")
    with pytest.raises(errors.Error):  # a field agent is not given a ward
        assign(world, agent, "ward", "S1", "L1", "W1")
    with pytest.raises(errors.Error):  # a coordinator is not given a polling unit
        assign(world, chain["ward"], "polling_unit", "S1", "L1", "W1", "SY-31")
    with pytest.raises(errors.Error):  # a viewer gets no area
        assign(world, viewer, "state", "S1")
    with pytest.raises(errors.Error):  # an LGA needs its name
        assign(world, chain["lga"], "lga", "S1")
    with pytest.raises(errors.Error):  # only admins assign
        assign(world, chain["lga"], "lga", "S2", "L2", who=world.a.users["analyst"])
    with psycopg.connect(world.dsn, row_factory=dict_row, autocommit=True) as c:
        with pytest.raises(errors.Error), as_user(c, world.a.users["admin"]) as u:
            u.execute("UPDATE public.area_assignments SET state = 'S2' WHERE user_id = %s", (agent,))
        with as_user(c, world.b.users["admin"]) as u:  # another organisation sees none of it
            assert u.execute("SELECT count(*) AS n FROM public.area_assignments WHERE org_id = %s",
                             (world.a.id,)).fetchone()["n"] == 0  # fmt: skip
        # Changing someone's role drops their areas (a field agent made viewer keeps no polling units).
        with as_user(c, world.a.users["admin"], commit=True) as u:
            u.execute("SELECT public.change_member_role(%s, %s, 'viewer')", (world.a.id, agent))
        assert c.execute("SELECT count(*) AS n FROM public.area_assignments WHERE user_id = %s",
                         (agent,)).fetchone()["n"] == 0  # fmt: skip
