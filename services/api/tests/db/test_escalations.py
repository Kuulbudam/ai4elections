"""Phase 14: Escalate → routed to admins → notified → resolved with a reason → the raiser is notified.

Evidence rules checked on the real schema: history is append-only, an escalation is resolved once, notifications can
only be marked read by their owner, and another organisation sees nothing."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row

from .conftest import as_service, as_user
from .test_ingestion import H


def new_finding(world, org=None, title="Unusual turnout at SY-9", area=("S1", "L1", "W1", "SY-9")) -> str:
    o = org or world.a
    with psycopg.connect(world.dsn, row_factory=dict_row, autocommit=True) as c, as_service(c, o.users["admin"]) as s:
        vid = s.execute("SELECT dataset_version_id FROM public.findings WHERE id = %s",
                        (o.rows["findings"],)).fetchone()["dataset_version_id"]  # fmt: skip
        run = s.execute("SELECT analytical_run_id FROM public.findings WHERE id = %s",
                        (o.rows["findings"],)).fetchone()["analytical_run_id"]  # fmt: skip
        return str(s.execute(
            """INSERT INTO public.findings (org_id, project_id, dataset_version_id, analytical_run_id, category,
               indicator, severity, confidence, title, explanation, limitations, provenance, state, lga, ward, pu_code)
               VALUES (%s,%s,%s,%s,'statistical_anomaly','turnout_rate','very_high','high',%s,'x','y','{}',
                       %s,%s,%s,%s) RETURNING id""",
            (o.id, o.rows["projects"], vid, run, title, *area),
        ).fetchone()["id"])  # fmt: skip


def escalate(api, world, fid, who="analyst", reason="Turnout 98% needs a senior decision before Friday."):
    return api.post(f"/findings/{fid}/reviews", json={"decision": "escalated", "reason": reason},
                    headers=H(world.a.users[who], str(uuid.uuid4())))  # fmt: skip


def inbox(world, uid):
    with psycopg.connect(world.dsn, row_factory=dict_row) as c:
        return c.execute("SELECT kind, title, link, read_at FROM public.notifications WHERE user_id = %s "
                         "ORDER BY created_at", (uid,)).fetchall()  # fmt: skip


def test_escalate_routes_to_admins_and_resolve_notifies_the_raiser(api, world):
    fid = new_finding(world)
    r = escalate(api, world, fid)
    assert r.status_code == 201, r.text
    eid = r.json()["escalation_id"]
    assert eid

    # The admin is told; the analyst who raised it is not told about their own escalation.
    admin, analyst = world.a.users["admin"], world.a.users["analyst"]
    got = [n for n in inbox(world, admin) if n["link"] == f"/escalations/{eid}"]
    assert len(got) == 1 and got[0]["kind"] == "escalation_raised" and got[0]["title"].startswith("Escalated to you")
    assert not [n for n in inbox(world, analyst) if n["link"] == f"/escalations/{eid}"]

    e = api.get(f"/escalations/{eid}", headers=H(analyst)).json()
    assert e["status"] == "open" and e["current_level"] == "admin" and e["area"]["pu_code"] == "SY-9"
    assert e["can_resolve"] is False and [x["kind"] for x in e["events"]] == ["raised"]
    assert api.get(f"/escalations/{eid}", headers=H(admin)).json()["can_resolve"] is True

    # Escalating the same finding again while it is open does not create a second one.
    assert escalate(api, world, fid).json()["escalation_id"] == eid

    # Only an admin may resolve; a reason is required.
    for who in ("analyst", "researcher", "viewer"):
        x = api.post(f"/escalations/{eid}/resolve", json={"note": "Looks fine to me now."},
                     headers=H(world.a.users[who], str(uuid.uuid4())))  # fmt: skip
        assert x.status_code == 403, who
    assert api.post(f"/escalations/{eid}/resolve", json={"note": "ok"},
                    headers=H(admin, str(uuid.uuid4()))).status_code == 400  # fmt: skip
    done = api.post(f"/escalations/{eid}/resolve", json={"note": "Checked the EC8A: a cancelled poll; noted."},
                    headers=H(admin, str(uuid.uuid4())))  # fmt: skip
    assert done.status_code == 200, done.text
    d = done.json()
    assert d["status"] == "resolved" and d["resolution"].startswith("Checked the EC8A")
    assert [x["kind"] for x in d["events"]] == ["raised", "resolved"]
    assert [n["kind"] for n in inbox(world, analyst) if n["link"] == f"/escalations/{eid}"] == ["escalation_resolved"]

    again = api.post(f"/escalations/{eid}/resolve", json={"note": "Second try at resolving."},
                     headers=H(admin, str(uuid.uuid4())))  # fmt: skip
    assert again.status_code == 409
    up = api.post(f"/escalations/{eid}/send-up", json={"note": "Please look at this."},
                  headers=H(admin, str(uuid.uuid4())))  # fmt: skip
    assert up.status_code == 409

    # A new escalation of the same finding after it was resolved is a new item.
    assert escalate(api, world, fid).json()["escalation_id"] != eid

    listed = api.get("/escalations", params={"project_id": world.a.rows["projects"], "status": "resolved"},
                     headers=H(world.a.users["viewer"])).json()["items"]  # fmt: skip
    assert eid in [x["id"] for x in listed]


def test_other_organisations_see_nothing(api, world):
    fid = new_finding(world, title="Unusual share at SY-10")
    eid = escalate(api, world, fid).json()["escalation_id"]
    outsider = world.b.users["admin"]
    assert api.get(f"/escalations/{eid}", headers=H(outsider)).status_code == 404
    assert api.post(f"/escalations/{eid}/resolve", json={"note": "Not my organisation."},
                    headers=H(outsider, str(uuid.uuid4()))).status_code == 404  # fmt: skip
    assert api.get("/escalations", params={"project_id": world.a.rows["projects"]},
                   headers=H(outsider)).status_code == 404  # fmt: skip
    with psycopg.connect(world.dsn, row_factory=dict_row) as c, as_user(c, outsider) as u:
        assert u.execute("SELECT count(*) AS n FROM public.escalations WHERE id = %s", (eid,)).fetchone()["n"] == 0
        assert u.execute("SELECT count(*) AS n FROM public.escalation_overview WHERE id = %s",
                         (eid,)).fetchone()["n"] == 0  # fmt: skip


def test_history_cannot_be_changed_and_resolves_once(api, world):
    fid = new_finding(world, title="Unusual turnout at SY-11")
    eid = escalate(api, world, fid).json()["escalation_id"]
    admin = world.a.users["admin"]
    with psycopg.connect(world.dsn, row_factory=dict_row, autocommit=True) as c:
        for sql in ("UPDATE public.escalation_events SET note = 'edited' WHERE escalation_id = %s",
                    "DELETE FROM public.escalation_events WHERE escalation_id = %s",
                    "UPDATE public.escalations SET reason = 'edited' WHERE id = %s",
                    "DELETE FROM public.escalations WHERE id = %s"):  # fmt: skip
            with pytest.raises(errors.Error):
                with as_service(c, admin) as s:
                    s.execute(sql, (eid,))
        with as_service(c, admin) as s:
            s.execute(
                """INSERT INTO public.escalation_events (org_id, escalation_id, kind, level, actor_id, note)
                         VALUES (%s,%s,'resolved','admin',%s,'First resolution.')""",
                (world.a.id, eid, admin),
            )
        with pytest.raises(errors.Error):
            with as_service(c, admin) as s:
                s.execute(
                    """INSERT INTO public.escalation_events (org_id, escalation_id, kind, level, actor_id, note)
                             VALUES (%s,%s,'resolved','admin',%s,'Second resolution.')""",
                    (world.a.id, eid, admin),
                )
        # Browsers cannot write escalations at all (only the API, which applies the rules).
        with pytest.raises(errors.Error):
            with as_user(c, admin) as u:
                u.execute(
                    """INSERT INTO public.escalation_events (org_id, escalation_id, kind, level, actor_id, note)
                             VALUES (%s,%s,'comment','admin',%s,'From the browser.')""",
                    (world.a.id, eid, admin),
                )


def test_notifications_are_private_and_only_marked_read(api, world):
    fid = new_finding(world, title="Unusual turnout at SY-12")
    eid = escalate(api, world, fid).json()["escalation_id"]
    admin, analyst = world.a.users["admin"], world.a.users["analyst"]
    with psycopg.connect(world.dsn, row_factory=dict_row, autocommit=True) as c:
        with as_user(c, analyst) as u:  # someone else's notification is invisible
            assert u.execute("SELECT count(*) AS n FROM public.notifications WHERE user_id = %s",
                             (admin,)).fetchone()["n"] == 0  # fmt: skip
        with as_user(c, admin, commit=True) as u:
            nid = u.execute("SELECT id FROM public.notifications WHERE link = %s", (f"/escalations/{eid}",)
                            ).fetchone()["id"]  # fmt: skip
            u.execute("UPDATE public.notifications SET read_at = now() WHERE id = %s", (nid,))
        with pytest.raises(errors.Error):
            with as_user(c, admin) as u:
                u.execute("UPDATE public.notifications SET title = 'changed' WHERE id = %s", (nid,))
        with pytest.raises(errors.Error):
            with as_user(c, admin) as u:
                u.execute("DELETE FROM public.notifications WHERE id = %s", (nid,))
        with as_user(c, analyst) as u:  # marking another person's notification changes nothing
            assert u.execute("UPDATE public.notifications SET read_at = now() WHERE user_id = %s", (admin,)
                             ).rowcount == 0  # fmt: skip
