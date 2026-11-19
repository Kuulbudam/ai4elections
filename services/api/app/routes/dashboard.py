"""GET /dataset-versions/{id}/dashboard: compact data for the analysis dashboard charts."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from app.auth import CurrentUser
from app.contracts.models import VersionDashboard
from app.deps import authorize, get_db, rate_limited_user, row_or_404

router = APIRouter()
ALL_ROLES = {"admin", "analyst", "researcher", "viewer"}

# Same counting rules as the analysis engine and GET /areas.
_UNITS = """
    SELECT coalesce(state, '(missing)') || ' / ' || coalesce(lga, '(missing)') AS area,
      CASE WHEN NOT ('all' = ANY(excluded_from) OR 'turnout_rate' = ANY(excluded_from))
                AND registered_voters > 0 AND accredited_voters IS NOT NULL AND state IS NOT NULL AND lga IS NOT NULL
                AND ward IS NOT NULL
           THEN round(accredited_voters::numeric / registered_voters, 4) END AS turnout,
      CASE WHEN NOT ('all' = ANY(excluded_from) OR 'leading_party_share' = ANY(excluded_from))
                AND valid_votes > 0 AND party_votes <> '{}'::jsonb AND state IS NOT NULL AND lga IS NOT NULL
                AND ward IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM jsonb_each(party_votes) e WHERE e.value = 'null'::jsonb)
           THEN round((SELECT max((e.value)::numeric) FROM jsonb_each(party_votes) e) / valid_votes, 4) END AS share
    FROM public.result_records WHERE dataset_version_id = %s
    ORDER BY area, raw_row_id
"""


@router.get("/dataset-versions/{version_id}/dashboard", response_model=VersionDashboard)
def dashboard(
    version_id: uuid.UUID,
    user: CurrentUser = Depends(rate_limited_user),
    db: psycopg.Connection = Depends(get_db),
) -> Any:
    v = row_or_404(
        db.execute(
            "SELECT id, org_id, validated_at, validation_summary, latest_analysis_run_id FROM public.dataset_versions "
            "WHERE id = %s",
            (str(version_id),),
        ).fetchone()
    )
    authorize(db, str(v["org_id"]), user, ALL_ROLES)
    if v["validated_at"] is None:
        raise HTTPException(status_code=409, detail="Run validation first")
    rows = db.execute(_UNITS, (str(v["id"]),)).fetchall()
    lgas: list[str] = []
    index: dict[str, int] = {}
    units = []
    for r in rows:
        i = index.setdefault(r["area"], len(lgas))
        if i == len(lgas):
            lgas.append(r["area"])
        units.append([i, float(r["turnout"]) if r["turnout"] is not None else None,
                      float(r["share"]) if r["share"] is not None else None])  # fmt: skip

    vs = v["validation_summary"] or {}
    kpi: dict[str, int] = {"units": len(units),
                           "turnout_counted": sum(1 for u in units if u[1] is not None),
                           "share_counted": sum(1 for u in units if u[2] is not None)}  # fmt: skip
    flagged: list[dict[str, Any]] = []
    severity: dict[str, dict[str, int]] = {}
    dq: dict[str, int] = {}
    run = v["latest_analysis_run_id"]
    if run:
        fs = db.execute(
            """SELECT id::text AS id, category, indicator, rule_id, pu_code, state, lga, ward, severity, statistics,
                      review_status
               FROM public.finding_overview WHERE analytical_run_id = %s ORDER BY id""",
            (str(run),),
        ).fetchall()
        for f in fs:
            if f["category"] == "data_quality_issue":
                dq[f["rule_id"]] = dq.get(f["rule_id"], 0) + 1
                continue
            st = f["statistics"] or {}
            area = f"{f['state'] or '(missing)'} / {f['lga'] or '(missing)'}"
            i = index.setdefault(area, len(lgas))
            if i == len(lgas):
                lgas.append(area)
            sev = severity.setdefault(f["indicator"], {"medium": 0, "high": 0, "very_high": 0})
            sev[f["severity"]] = sev.get(f["severity"], 0) + 1
            flagged.append({"finding_id": f["id"], "pu_code": f["pu_code"], "ward": f["ward"], "lga": i,
                            "indicator": f["indicator"], "value": float(st.get("value", 0)),
                            "median": float(st.get("median", 0)), "robust_z": float(st.get("robust_z", 0)),
                            "severity": f["severity"], "review_status": f["review_status"]})  # fmt: skip
        kpi |= {
            "unusual_turnout": sum(severity.get("turnout_rate", {}).values()),
            "unusual_share": sum(severity.get("leading_party_share", {}).values()),
            "very_high": sum(1 for f in flagged if f["severity"] == "very_high"),
            "data_quality": sum(dq.values()),
            "to_review": sum(1 for f in flagged if f["review_status"] == "unreviewed"),
        }
    else:
        dq = dict(vs.get("rows_by_rule") or {})
    return VersionDashboard(
        dataset_version_id=v["id"], analytical_run_id=run, lgas=lgas, units=units, flagged=flagged, kpi=kpi,
        severity=severity, data_quality_by_rule=dict(sorted(dq.items())),
    ).model_dump(mode="json")  # fmt: skip
