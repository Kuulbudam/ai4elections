"""GET /areas: state / LGA / ward totals for one validated dataset version (ADR-0004: no maps).

Counting rules match the analysis engine, so an area's turnout is built from exactly the units the
analysis could score: a unit counts towards turnout when validation did not exclude it from turnout
and registered voters > 0; towards vote share when it was not excluded from vote share, valid votes
> 0 and every party count is present. All sums are done in Postgres on integers; rates are divided
once at the end.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import CurrentUser
from app.contracts.models import AreaList
from app.deps import authorize, get_db, rate_limited_user, row_or_404

router = APIRouter()
ALL_ROLES = {"admin", "analyst", "researcher", "viewer"}
KEYS = {"state": ["state"], "lga": ["state", "lga"], "ward": ["state", "lga", "ward"]}

# Fixed SQL fragments only; every value is passed as a parameter.
_UNITS = """
    SELECT state, lga, ward, registered_voters AS reg, accredited_voters AS acc, valid_votes AS val, party_votes AS pv,
      (NOT ('all' = ANY(excluded_from) OR 'turnout_rate' = ANY(excluded_from))
        AND registered_voters IS NOT NULL AND accredited_voters IS NOT NULL AND registered_voters > 0) AS t_ok,
      (NOT ('all' = ANY(excluded_from) OR 'leading_party_share' = ANY(excluded_from))
        AND valid_votes IS NOT NULL AND valid_votes > 0 AND party_votes <> '{}'::jsonb
        AND NOT EXISTS (SELECT 1 FROM jsonb_each(party_votes) e WHERE e.value = 'null'::jsonb)) AS s_ok
    FROM public.result_records
    WHERE dataset_version_id = %(vid)s
      AND (%(state)s::text IS NULL OR lower(state) = lower(%(state)s))
      AND (%(lga)s::text IS NULL OR lower(lga) = lower(%(lga)s))
"""


def _rate(num: int, den: int) -> str | None:
    return f"{num / den:.6f}" if den > 0 else None


def area_list(
    db: psycopg.Connection, v: dict[str, Any], level: str, state: str | None, lga: str | None
) -> dict[str, Any]:
    """Shared by the endpoint and (later) the data assistant's lookup tools."""
    keys = KEYS[level]
    cols = ", ".join(keys)
    params = {"vid": str(v["id"]), "state": state, "lga": lga, "run": v["latest_analysis_run_id"]}
    totals = db.execute(
        f"""SELECT {cols}, count(*) AS units,
                   count(*) FILTER (WHERE t_ok) AS t_units,
                   coalesce(sum(reg) FILTER (WHERE t_ok), 0) AS reg, coalesce(sum(acc) FILTER (WHERE t_ok), 0) AS acc,
                   count(*) FILTER (WHERE s_ok) AS s_units, coalesce(sum(val) FILTER (WHERE s_ok), 0) AS val
            FROM ({_UNITS}) u GROUP BY {cols} ORDER BY {cols}""",  # noqa: S608
        params,
    ).fetchall()
    parties = db.execute(
        f"""SELECT {cols}, e.key AS party, sum((e.value)::bigint) AS votes
            FROM ({_UNITS}) u, jsonb_each(u.pv) e WHERE u.s_ok GROUP BY {cols}, e.key""",  # noqa: S608
        params,
    ).fetchall()
    findings = []
    if v["latest_analysis_run_id"]:
        findings = db.execute(
            f"""SELECT {cols},
                   count(*) FILTER (WHERE category = 'statistical_anomaly' AND indicator = 'turnout_rate') AS t,
                   count(*) FILTER (WHERE category = 'statistical_anomaly' AND indicator = 'leading_party_share') AS s,
                   count(*) FILTER (WHERE category = 'statistical_anomaly' AND severity = 'very_high') AS vh,
                   count(*) FILTER (WHERE category = 'data_quality_issue') AS dq,
                   count(*) FILTER (WHERE review_status = 'unreviewed') AS unrev
                FROM public.finding_overview
                WHERE analytical_run_id = %(run)s
                  AND (%(state)s::text IS NULL OR lower(state) = lower(%(state)s))
                  AND (%(lga)s::text IS NULL OR lower(lga) = lower(%(lga)s))
                GROUP BY {cols}""",  # noqa: S608
            params,
        ).fetchall()
    units = db.execute(
        "SELECT lower(state) AS s, lower(lga) AS l, lower(ward) AS w FROM public.admin_units WHERE project_id = %s",
        (str(v["project_id"]),),
    ).fetchall()
    ref = {
        "state": {(u["s"],) for u in units},
        "lga": {(u["s"], u["l"]) for u in units},
        "ward": {(u["s"], u["l"], u["w"]) for u in units},
    }[level]

    def key(r: dict[str, Any]) -> tuple:
        return tuple(r[k] for k in keys)

    party_by: dict[tuple, dict[str, int]] = {}
    for r in parties:
        party_by.setdefault(key(r), {})[r["party"]] = int(r["votes"])
    find_by = {key(r): r for r in findings}
    items = []
    for r in totals:
        k = key(r)
        pt = dict(sorted(party_by.get(k, {}).items()))
        top = max(pt.values()) if pt else None
        leaders = sorted(p for p, n in pt.items() if n == top) if pt else []
        f = find_by.get(k)
        labels = {kk: r[kk] for kk in keys}
        in_ref = None
        if units:
            in_ref = None if any(x is None for x in k) else tuple(" ".join(x.split()).lower() for x in k) in ref
        items.append({
            "state": labels.get("state"), "lga": labels.get("lga"), "ward": labels.get("ward"),
            "in_reference_list": in_ref, "units": r["units"],
            "turnout": {"units": r["t_units"], "registered_voters": int(r["reg"]), "accredited_voters": int(r["acc"]),
                        "rate": _rate(int(r["acc"]), int(r["reg"]))},
            "vote_share": {"units": r["s_units"], "valid_votes": int(r["val"]), "party_totals": pt,
                           "leading_party": leaders[0] if leaders else None,
                           "leading_share": _rate(top, int(r["val"])) if top is not None else None},
            "findings": None if not v["latest_analysis_run_id"] else {
                "unusual_turnout": f["t"] if f else 0, "unusual_share": f["s"] if f else 0,
                "very_high": f["vh"] if f else 0, "data_quality": f["dq"] if f else 0,
                "unreviewed": f["unrev"] if f else 0,
            },
        })  # fmt: skip
    return AreaList(
        dataset_version_id=v["id"],
        analytical_run_id=v["latest_analysis_run_id"],
        level=level,
        state=state,
        lga=lga,
        reference_list_used=bool(units),
        items=items,
    ).model_dump(mode="json")


@router.get("/areas", response_model=AreaList)
def list_areas(
    dataset_version_id: uuid.UUID,
    level: Literal["state", "lga", "ward"],
    state: str | None = Query(None, max_length=200),
    lga: str | None = Query(None, max_length=200),
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    v = row_or_404(
        db.execute(
            "SELECT id, org_id, project_id, validated_at, latest_analysis_run_id FROM public.dataset_versions "
            "WHERE id = %s",
            (str(dataset_version_id),),
        ).fetchone()
    )
    authorize(db, str(v["org_id"]), user, ALL_ROLES)
    if v["validated_at"] is None:
        raise HTTPException(status_code=409, detail="Run validation before viewing area totals")
    clean = lambda s: " ".join(s.split()) if s and s.strip() else None  # noqa: E731
    state, lga = clean(state), clean(lga)
    if lga and not state:
        raise HTTPException(status_code=400, detail="Choose a state before an LGA")
    return area_list(db, v, level, state, lga)
