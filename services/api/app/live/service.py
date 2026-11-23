"""Election-day live view (Phase 17): what observers have reported so far, counted by the server.

Every number here is a count of stored reports or a sum of figures a person confirmed from a result sheet. Nothing is
estimated or projected. The same summary goes into the submission pack, so the screen and the pack always agree.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import psycopg

from app.field.payloads import CATEGORY_LABEL

COUNTS = ("registered_voters", "accredited_voters", "valid_votes", "rejected_votes", "total_cast")


def _key(v: str | None) -> str:
    return " ".join(str(v or "").split()).upper()


def result_rows(db: psycopg.Connection, project_id: str) -> list[dict[str, Any]]:
    """The latest confirmed sheet for each polling unit, with its area (from the field report when there is one)."""
    rows = db.execute(
        """SELECT DISTINCT ON (upper(btrim(coalesce(s.confirmed->>'pu_code', s.pu_code))))
                  s.id::text AS id, coalesce(s.confirmed->>'pu_code', s.pu_code) AS pu_code,
                  coalesce(f.state, s.confirmed->>'state') AS state, coalesce(f.lga, s.confirmed->>'lga') AS lga,
                  coalesce(f.ward, s.confirmed->>'ward') AS ward, s.confirmed, s.checks, s.image_sha256,
                  s.confirmed_at, (f.id IS NOT NULL) AS from_field
           FROM public.result_sheets s
           LEFT JOIN public.field_reports f ON f.sheet_id = s.id AND f.kind = 'result'
           WHERE s.project_id = %s AND s.status = 'confirmed'
             AND coalesce(s.confirmed->>'pu_code', s.pu_code) IS NOT NULL
           ORDER BY upper(btrim(coalesce(s.confirmed->>'pu_code', s.pu_code))), s.confirmed_at DESC""",
        (project_id,),
    ).fetchall()
    out = []
    for r in rows:
        c = r["confirmed"] or {}
        checks = [x for x in (r["checks"] or []) if isinstance(x, dict)]
        failed = [x.get("title") or x.get("id") for x in checks if x.get("ok") is False]
        out.append({**r, "party_votes": c.get("party_votes") or [], **{k: c.get(k) for k in COUNTS},
                    "checks_failed": failed})  # fmt: skip
    return out


def summary(db: psycopg.Connection, org_id: str, project_id: str) -> dict[str, Any]:
    assigned = db.execute(
        """SELECT DISTINCT upper(btrim(pu_code)) AS pu, state, lga, ward FROM public.area_assignments
           WHERE org_id = %s AND level = 'polling_unit'""",
        (org_id,),
    ).fetchall()
    reports = db.execute(
        """SELECT id::text AS id, kind, upper(btrim(pu_code)) AS pu, pu_code, state, lga, ward, payload, severity,
                  captured_at, received_at
           FROM public.field_reports WHERE project_id = %s ORDER BY received_at DESC""",
        (project_id,),
    ).fetchall()
    escalated = {
        r["source_id"]: r
        for r in db.execute(
            """SELECT e.source_id::text AS source_id, e.id::text AS id, o.status FROM public.escalations e
               JOIN public.escalation_overview o ON o.id = e.id
               WHERE e.project_id = %s AND e.source_kind = 'field_report'""",
            (project_id,),
        ).fetchall()
    }
    open_escalations = db.execute(
        "SELECT count(*) AS n FROM public.escalation_overview WHERE project_id = %s AND status = 'open'", (project_id,)
    ).fetchone()["n"]
    results = result_rows(db, project_id)

    by_kind: dict[str, set[str]] = defaultdict(set)
    for r in reports:
        by_kind[r["kind"]].add(r["pu"])
    for r in results:
        by_kind["result"].add(_key(r["pu_code"]))
    reporting = set().union(*by_kind.values()) if by_kind else set()

    incidents = [r for r in reports if r["kind"] == "incident"]
    cats: dict[str, int] = defaultdict(int)
    for r in incidents:
        cats[r["payload"].get("category", "other")] += 1
    recent = [
        {"id": r["id"], "pu_code": r["pu_code"], "state": r["state"], "lga": r["lga"], "ward": r["ward"],
         "category": r["payload"].get("category", "other"),
         "label": CATEGORY_LABEL.get(r["payload"].get("category", "other"), "Other incident"),
         "severity": r["severity"], "description": r["payload"].get("description", ""),
         "captured_at": r["captured_at"], "escalation_id": (escalated.get(r["id"]) or {}).get("id"),
         "escalation_status": (escalated.get(r["id"]) or {}).get("status")}
        for r in incidents[:25]
    ]  # fmt: skip

    parties: dict[str, int] = defaultdict(int)
    totals = dict.fromkeys(COUNTS, 0)
    for r in results:
        for p in r["party_votes"]:
            parties[str(p.get("party", "")).upper()] += int(p.get("votes") or 0)
        for k in COUNTS:
            totals[k] += int(r[k] or 0)

    # one row per LGA: units assigned, reporting, incidents, critical, results in
    areas: dict[tuple[str, str], dict[str, Any]] = {}

    def area(state: str | None, lga: str | None) -> dict[str, Any]:
        k = (state or "Unknown", lga or "Unknown")
        if k not in areas:
            areas[k] = {"state": k[0], "lga": k[1], "assigned": 0, "reporting": set(), "incidents": 0,
                        "critical": 0, "results": 0}  # fmt: skip
        return areas[k]

    for a in assigned:
        area(a["state"], a["lga"])["assigned"] += 1
    for r in reports:
        x = area(r["state"], r["lga"])
        x["reporting"].add(r["pu"])
        if r["kind"] == "incident":
            x["incidents"] += 1
            x["critical"] += r["severity"] == "critical"
    for r in results:
        x = area(r["state"], r["lga"])
        x["reporting"].add(_key(r["pu_code"]))
        x["results"] += 1
    area_rows = sorted(
        ({**v, "reporting": len(v["reporting"])} for v in areas.values()),
        key=lambda v: (-v["critical"], -v["incidents"], v["state"], v["lga"]),
    )

    return {
        "generated_at": datetime.now(UTC),
        "coverage": {
            "assigned_units": len({a["pu"] for a in assigned}),
            "reporting_units": len(reporting),
            "opened": len(by_kind["arrival"]),
            "accreditation": len(by_kind["accreditation"]),
            "results": len(by_kind["result"]),
            "closing": len(by_kind["closing"]),
        },
        "incidents": {
            "total": len(incidents),
            "critical": sum(1 for r in incidents if r["severity"] == "critical"),
            "open_escalations": open_escalations,
            "by_category": sorted(
                (
                    {"category": k, "label": CATEGORY_LABEL.get(k, "Other incident"), "count": n}
                    for k, n in cats.items()
                ),  # fmt: skip
                key=lambda x: -x["count"],
            ),
            "recent": recent,
        },
        "results": {
            "units": len(results),
            "with_failed_checks": sum(1 for r in results if r["checks_failed"]),
            "party_totals": sorted(
                ({"party": k, "votes": v} for k, v in parties.items() if k), key=lambda x: -x["votes"]
            ),
            **totals,
        },
        "areas": area_rows[:200],
        "last_report_at": reports[0]["received_at"] if reports else None,
    }
