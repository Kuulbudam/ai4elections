"""Escalation: raise, route, send up, resolve, notify.

An escalation goes to the lowest level above the person who raised it whose area covers the item and that has at
least one coordinator; failing that, to the organisation's admins. Each step is an append-only event; the current
level and status come from those events (view escalation_overview). Notifications go to everyone at the level.

Phase 14 routes everything to admins; Phase 15 adds coordinators (area_assignments) without changing callers.
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import HTTPException
from psycopg.types.json import Jsonb

LEVELS = ["ward", "lga", "state", "admin"]
LEVEL_NAME = {"ward": "ward coordinators", "lga": "LGA coordinators", "state": "state coordinators", "admin": "admins"}


def _norm(v: Any) -> str:
    return " ".join(str(v or "").split()).casefold()


def _has_assignments(db: psycopg.Connection) -> bool:
    return bool(db.execute("SELECT to_regclass('public.area_assignments') IS NOT NULL AS ok").fetchone()["ok"])


def coordinators(db: psycopg.Connection, org_id: str, level: str, area: dict[str, Any]) -> list[str]:
    """User ids of coordinators assigned at `level` whose area covers `area` (empty before Phase 15)."""
    if level == "admin" or not _has_assignments(db):
        return []
    rows = db.execute(
        """SELECT a.user_id::text AS user_id, a.state, a.lga, a.ward FROM public.area_assignments a
           JOIN public.memberships m ON m.org_id = a.org_id AND m.user_id = a.user_id AND m.role = 'coordinator'
           WHERE a.org_id = %s AND a.level = %s""",
        (org_id, level),
    ).fetchall()
    out = []
    for r in rows:
        keys = {"ward": ("state", "lga", "ward"), "lga": ("state", "lga"), "state": ("state",)}[level]
        if all(_norm(r[k]) == _norm(area.get(k)) and area.get(k) for k in keys):
            out.append(r["user_id"])
    return sorted(set(out))


def admins(db: psycopg.Connection, org_id: str) -> list[str]:
    return [r["user_id"] for r in db.execute(
        "SELECT user_id::text AS user_id FROM public.memberships WHERE org_id = %s AND role = 'admin'", (org_id,)
    ).fetchall()]  # fmt: skip


def level_of(db: psycopg.Connection, org_id: str, user_id: str, area: dict[str, Any]) -> str | None:
    """The lowest level at which this person is a coordinator for the area (None if not a coordinator for it)."""
    for lvl in LEVELS[:-1]:
        if user_id in coordinators(db, org_id, lvl, area):
            return lvl
    return None


def route(db: psycopg.Connection, org_id: str, area: dict[str, Any], above: str | None) -> tuple[str, list[str]]:
    """First level strictly above `above` (or from the bottom) with someone to act; admins at the top."""
    start = 0 if above is None else LEVELS.index(above) + 1
    for lvl in LEVELS[start:-1]:
        people = coordinators(db, org_id, lvl, area)
        if people:
            return lvl, people
    return "admin", admins(db, org_id)


def notify(db: psycopg.Connection, org_id: str, users: list[str], kind: str, title: str, body: str, link: str) -> None:
    for u in sorted(set(users)):
        db.execute(
            """INSERT INTO public.notifications (org_id, user_id, kind, title, body, link)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (org_id, u, kind, title[:300], body[:2000], link),
        )


def raise_escalation(db: psycopg.Connection, *, org_id: str, project_id: str, source_kind: str, source_id: str,
                     title: str, area: dict[str, Any], raised_by: str, reason: str) -> str:  # fmt: skip
    """Create the escalation, route it and notify (call inside a transaction). Re-raising an open one returns it."""
    open_one = db.execute(
        """SELECT id::text AS id FROM public.escalation_overview
           WHERE source_kind = %s AND source_id = %s AND status = 'open' LIMIT 1""",
        (source_kind, source_id),
    ).fetchone()
    if open_one:
        return open_one["id"]
    area = {k: str(v) for k, v in area.items() if v not in (None, "")}
    raiser_level = level_of(db, org_id, raised_by, area)
    level, people = route(db, org_id, area, raiser_level)
    eid = db.execute(
        """INSERT INTO public.escalations (org_id, project_id, source_kind, source_id, title, area, raised_by, reason)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id::text AS id""",
        (org_id, project_id, source_kind, source_id, title[:300], Jsonb(area), raised_by, reason),
    ).fetchone()["id"]
    db.execute(
        """INSERT INTO public.escalation_events (org_id, escalation_id, kind, level, actor_id, note)
           VALUES (%s,%s,'raised',%s,%s,%s)""",
        (org_id, eid, level, raised_by, reason),
    )
    notify(db, org_id, [p for p in people if p != raised_by], "escalation_raised", f"Escalated to you: {title}",
           reason, f"/escalations/{eid}")  # fmt: skip
    return eid


def _current(db: psycopg.Connection, escalation_id: str) -> dict[str, Any]:
    e = db.execute(
        """SELECT id::text AS id, org_id::text AS org_id, project_id::text AS project_id, title, area,
                  raised_by::text AS raised_by, current_level, status
           FROM public.escalation_overview WHERE id = %s""",
        (escalation_id,),
    ).fetchone()
    if e is None:
        raise HTTPException(status_code=404, detail="Not found")
    return e


def permissions(db: psycopg.Connection, e: dict[str, Any], user_id: str, role: str | None) -> tuple[bool, bool]:
    """(can_resolve, can_send_up) for this person on this escalation."""
    if e["status"] != "open" or role is None:
        return False, False
    at_level = e["current_level"] != "admin" and user_id in coordinators(db, e["org_id"], e["current_level"], e["area"])
    return role == "admin" or at_level, at_level


def resolve(db: psycopg.Connection, escalation_id: str, user_id: str, role: str | None, note: str) -> None:
    e = _current(db, escalation_id)
    if e["status"] != "open":
        raise HTTPException(status_code=409, detail="This escalation is already resolved.")
    can_resolve, _ = permissions(db, e, user_id, role)
    if not can_resolve:
        raise HTTPException(status_code=403, detail="Only admins or a coordinator it was escalated to can resolve it.")
    db.execute(
        """INSERT INTO public.escalation_events (org_id, escalation_id, kind, level, actor_id, note)
           VALUES (%s,%s,'resolved',%s,%s,%s)""",
        (e["org_id"], escalation_id, e["current_level"], user_id, note),
    )
    if e["raised_by"] != user_id:
        notify(db, e["org_id"], [e["raised_by"]], "escalation_resolved", f"Resolved: {e['title']}", note,
               f"/escalations/{escalation_id}")  # fmt: skip


def send_up(db: psycopg.Connection, escalation_id: str, user_id: str, role: str | None, note: str) -> str:
    e = _current(db, escalation_id)
    if e["status"] != "open":
        raise HTTPException(status_code=409, detail="This escalation is already resolved.")
    if e["current_level"] == "admin":
        raise HTTPException(status_code=409, detail="This is already with the admins, the top level.")
    _, can_send_up = permissions(db, e, user_id, role)
    if not can_send_up:
        raise HTTPException(status_code=403, detail="Only a coordinator it was escalated to can send it up.")
    level, people = route(db, e["org_id"], e["area"], e["current_level"])
    db.execute(
        """INSERT INTO public.escalation_events (org_id, escalation_id, kind, level, actor_id, note)
           VALUES (%s,%s,'sent_up',%s,%s,%s)""",
        (e["org_id"], escalation_id, level, user_id, note),
    )
    notify(db, e["org_id"], [p for p in people if p != user_id], "escalation_sent_up",
           f"Sent up to {LEVEL_NAME[level]}: {e['title']}", note, f"/escalations/{escalation_id}")  # fmt: skip
    return level
