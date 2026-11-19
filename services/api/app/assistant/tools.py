# ruff: noqa: E501  (tool descriptions and compact dict literals are kept on one line)
"""The data assistant's lookups. The model can ONLY call these; it never writes SQL.

Every lookup is bound to one project that the caller has already been authorised for (admin or
analyst). Any id the model passes is checked against that project before use, so a question can
never reach another project or organisation. Lookups return compact JSON plus citations (links to
the pages where a person can check each figure).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import psycopg

from app.routes.areas import area_list
from app.routes.findings import _SELECT, find_rows

MAX_FINDINGS = 20
SEVERITIES = ["very_high", "high", "medium", "low"]
DECISIONS = ["unreviewed", "acknowledged", "needs_more_evidence", "explained_by_data_error", "explained_by_context",
             "escalated"]  # fmt: skip


@dataclass
class Ctx:
    db: psycopg.Connection
    project_id: str
    org_id: str
    citations: list[dict[str, str]] = field(default_factory=list)

    def cite(self, kind: str, label: str, href: str) -> None:
        c = {"kind": kind, "label": label[:160], "href": href}
        if c not in self.citations:
            self.citations.append(c)


class ToolInputError(Exception):
    pass


def _uuid(v: Any, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, TypeError) as e:
        raise ToolInputError(f"{what} must be an id from an earlier lookup") from e


def _version(ctx: Ctx, version_id: Any) -> dict[str, Any]:
    v = ctx.db.execute(
        "SELECT id, org_id, project_id, original_filename, version_number, validated_at, validation_summary, "
        "latest_analysis_run_id FROM public.dataset_versions WHERE id = %s AND project_id = %s AND archived_at IS NULL",
        (_uuid(version_id, "dataset_version_id"), ctx.project_id),
    ).fetchone()
    if v is None:
        raise ToolInputError("No such dataset version in this project")
    return v


def _pct(x: Any) -> float | None:
    return None if x is None else round(float(x) * 100, 1)


# ------------------------------------------------------------------ lookups


def project_overview(ctx: Ctx) -> dict[str, Any]:
    p = ctx.db.execute(
        "SELECT election_name, election_year, country, election_level FROM public.projects WHERE id = %s",
        (ctx.project_id,),
    ).fetchone()
    vs = ctx.db.execute(
        """SELECT v.id, d.name AS dataset, v.original_filename, v.version_number, v.status, v.row_count,
                  v.validated_at IS NOT NULL AS validated, v.validation_summary, r.summary AS analysis
           FROM public.dataset_versions v JOIN public.datasets d ON d.id = v.dataset_id
           LEFT JOIN public.analytical_runs r ON r.id = v.latest_analysis_run_id
           WHERE v.project_id = %s AND v.archived_at IS NULL ORDER BY v.created_at""",
        (ctx.project_id,),
    ).fetchall()
    out = []
    for v in vs:
        vsum, a = v["validation_summary"] or {}, v["analysis"] or {}
        item: dict[str, Any] = {
            "dataset_version_id": str(v["id"]), "dataset": v["dataset"], "file": v["original_filename"],
            "version": v["version_number"], "status": v["status"], "rows": v["row_count"], "validated": v["validated"],
        }  # fmt: skip
        if vsum:
            item["validation"] = {"row_status": vsum.get("row_status"), "issues_by_rule": vsum.get("issues_by_rule"),
                                  "rows_by_rule": vsum.get("rows_by_rule")}  # fmt: skip
        if a:
            item["analysis"] = {
                ind: {k: a["indicators"][ind][k] for k in ("scored", "flagged", "flagged_by_severity", "not_scored")}
                for ind in a.get("indicators", {})
            } | {"data_quality_findings": a.get("data_quality_findings")}
        out.append(item)
        ctx.cite("version", f"{v['original_filename']} v{v['version_number']}", f"/versions/{v['id']}/analysis")
    return {"project": dict(p), "dataset_versions": out}


def area_totals(ctx: Ctx, dataset_version_id: Any, level: Any, state: Any = None, lga: Any = None) -> dict[str, Any]:
    if level not in ("state", "lga", "ward"):
        raise ToolInputError("level must be state, lga or ward")
    v = _version(ctx, dataset_version_id)
    if v["validated_at"] is None:
        raise ToolInputError("This version has not been validated yet")
    state = (str(state).strip() or None) if state else None
    lga = (str(lga).strip() or None) if lga else None
    if lga and not state:
        raise ToolInputError("give the state as well as the LGA")
    res = area_list(ctx.db, v, level, state, lga)
    items = []
    for a in res["items"][:80]:
        items.append({
            "state": a["state"], "lga": a["lga"], "ward": a["ward"], "polling_units": a["units"],
            "turnout_percent": _pct(a["turnout"]["rate"]), "turnout_units_counted": a["turnout"]["units"],
            "registered": a["turnout"]["registered_voters"], "accredited": a["turnout"]["accredited_voters"],
            "valid_votes": a["vote_share"]["valid_votes"], "party_totals": a["vote_share"]["party_totals"],
            "leading_party": a["vote_share"]["leading_party"],
            "leading_share_percent": _pct(a["vote_share"]["leading_share"]),
            "findings": a["findings"], "in_reference_list": a["in_reference_list"],
        })  # fmt: skip
    q = f"?state={state}" if state else ""
    q += f"&lga={lga}" if lga else ""
    ctx.cite("areas", f"Area totals ({level}{': ' + (lga or state) if state else ''})", f"/versions/{v['id']}/areas{q}")
    return {"level": level, "state": state, "lga": lga, "areas": items, "truncated": len(res["items"]) > 80,
            "note": "Totals count only units the analysis could use; party totals cover only parties in the file."}  # fmt: skip


def search_findings(ctx: Ctx, **f: Any) -> dict[str, Any]:
    allowed = {"category": ["statistical_anomaly", "data_quality_issue"],
               "indicator": ["turnout_rate", "leading_party_share"], "severity": SEVERITIES,
               "confidence": ["high", "medium", "low"], "review_status": DECISIONS}  # fmt: skip
    filters: dict[str, Any] = {}
    for k, ok in allowed.items():
        if f.get(k):
            if f[k] not in ok:
                raise ToolInputError(f"{k} must be one of {', '.join(ok)}")
            filters[k] = f[k]
    for k in ("state", "lga", "ward"):
        if f.get(k):
            filters[k] = str(f[k])[:200]
    run = None
    if f.get("dataset_version_id"):
        v = _version(ctx, f["dataset_version_id"])
        if not v["latest_analysis_run_id"]:
            raise ToolInputError("This version has not been analysed yet")
        run = v["latest_analysis_run_id"]
    limit = max(1, min(int(f.get("limit") or 10), MAX_FINDINGS))
    rows, _ = find_rows(ctx.db, ctx.project_id, run, filters, None, 5000)
    out = []
    for r in rows[:limit]:
        st = r["statistics"] or {}
        out.append({
            "finding_id": str(r["id"]), "title": r["title"], "category": r["category"], "indicator": r["indicator"],
            "rule_id": r["rule_id"], "pu_code": r["pu_code"], "state": r["state"], "lga": r["lga"], "ward": r["ward"],
            "severity": r["severity"], "confidence": r["confidence"], "review_status": r["review_status"],
            **({"value_percent": _pct(st.get("value")), "peer_median_percent": _pct(st.get("median")),
                "robust_z": st.get("robust_z"), "peers": (r["peer_group"] or {}).get("n")} if st else {}),
        })  # fmt: skip
        ctx.cite("finding", r["title"], f"/findings/{r['id']}")
    return {"matching": len(rows), "returned": len(out), "order": "most unusual first", "findings": out}


def get_finding(ctx: Ctx, finding_id: Any) -> dict[str, Any]:
    r = ctx.db.execute(f"{_SELECT} WHERE f.id = %s AND f.project_id = %s",  # noqa: S608
                       (_uuid(finding_id, "finding_id"), ctx.project_id)).fetchone()  # fmt: skip
    if r is None:
        raise ToolInputError("No such finding in this project")
    cols = list(r["columns"] or [])
    cells = list(r["cells"] or [])
    reviews = ctx.db.execute(
        "SELECT decision, reason, created_at FROM public.finding_reviews WHERE finding_id = %s "
        "ORDER BY created_at DESC LIMIT 5",
        (str(r["id"]),),
    ).fetchall()
    claims = ctx.db.execute(
        "SELECT status, statement FROM public.claims WHERE finding_id = %s ORDER BY created_at LIMIT 5", (str(r["id"]),)
    ).fetchall()
    st = r["statistics"] or {}
    ctx.cite("finding", r["title"], f"/findings/{r['id']}")
    return {
        "finding_id": str(r["id"]), "title": r["title"], "category": r["category"], "indicator": r["indicator"],
        "rule_id": r["rule_id"], "explanation": r["explanation"], "limitations": r["limitations"],
        "severity": r["severity"], "confidence": r["confidence"], "review_status": r["review_status"],
        "pu_code": r["pu_code"], "state": r["state"], "lga": r["lga"], "ward": r["ward"],
        "statistics": {"value_percent": _pct(st.get("value")), "peer_median_percent": _pct(st.get("median")),
                       "robust_z": st.get("robust_z"), "leading_party": st.get("leading_party")} if st else None,
        "peer_group": r["peer_group"],
        "source_row": {"row_number": r["source_row_number"],
                       "cells": {c: (cells[i] if i < len(cells) else "") for i, c in enumerate(cols)}},
        "reviews": [{"decision": x["decision"], "reason": x["reason"], "at": x["created_at"].isoformat()} for x in reviews],
        "claims": [{"status": x["status"], "statement": x["statement"]} for x in claims],
    }  # fmt: skip


def claims_overview(ctx: Ctx) -> dict[str, Any]:
    counts = ctx.db.execute(
        "SELECT status, count(*) AS n FROM public.claims WHERE project_id = %s GROUP BY status", (ctx.project_id,)
    ).fetchall()
    latest = ctx.db.execute(
        """SELECT c.status, c.statement, c.finding_id::text AS finding_id,
                  (SELECT count(*) FROM public.claim_evidence e WHERE e.claim_id = c.id) AS evidence_items
           FROM public.claims c WHERE c.project_id = %s ORDER BY c.created_at DESC LIMIT 10""",
        (ctx.project_id,),
    ).fetchall()
    ctx.cite("claims", "Claims register", f"/projects/{ctx.project_id}/claims")
    return {"counts": {r["status"]: r["n"] for r in counts}, "latest": [dict(x) for x in latest],
            "note": "unverified_allegation means NOT verified. Only verified_fact has passed independent review."}  # fmt: skip


def validation_issues(ctx: Ctx, dataset_version_id: Any, rule_id: Any = None, limit: Any = 10) -> dict[str, Any]:
    v = _version(ctx, dataset_version_id)
    if v["validated_at"] is None:
        raise ToolInputError("This version has not been validated yet")
    rule = str(rule_id).upper() if rule_id else None
    if rule and not (len(rule) == 4 and rule.startswith("V") and rule[1:].isdigit()):
        raise ToolInputError("rule_id looks like V003")
    n = max(1, min(int(limit or 10), 20))
    rows = ctx.db.execute(
        "SELECT rule_id, source_row_number, source_column, original_value, message FROM public.validation_issues "
        "WHERE dataset_version_id = %s AND (%s::text IS NULL OR rule_id = %s) ORDER BY source_row_number LIMIT %s",
        (str(v["id"]), rule, rule, n),
    ).fetchall()
    rules = ctx.db.execute(
        "SELECT rule_id, definition->>'description' AS description FROM public.validation_rules WHERE version = %s",
        ((v["validation_summary"] or {}).get("ruleset_version", "1.1.0"),),
    ).fetchall()
    ctx.cite("validation", f"Validation of {v['original_filename']}", f"/versions/{v['id']}/validation")
    s = v["validation_summary"] or {}
    return {"issues_by_rule": s.get("issues_by_rule"), "rows_by_rule": s.get("rows_by_rule"),
            "rule_descriptions": {r["rule_id"]: r["description"] for r in rules},
            "examples": [dict(r) for r in rows]}  # fmt: skip


# ------------------------------------------------------------------ registry for the model

_S = {"type": "string"}
SPECS: list[dict[str, Any]] = [
    {"name": "project_overview", "fn": project_overview,
     "description": "The project and its dataset versions, with validation and analysis summaries. Call this first to get dataset_version_id values.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "area_totals", "fn": area_totals,
     "description": "Totals by state, LGA or ward for one dataset version: polling units, turnout %, registered, accredited, valid votes, party totals, leading party, finding counts. Use state (and lga) to drill down.",
     "parameters": {"type": "object", "required": ["dataset_version_id", "level"], "additionalProperties": False,
                    "properties": {"dataset_version_id": _S, "level": {"type": "string", "enum": ["state", "lga", "ward"]},
                                   "state": _S, "lga": _S}}},
    {"name": "search_findings", "fn": search_findings,
     "description": "Search findings (statistical outliers and data quality issues), most unusual first. Returns the number matching and up to `limit` items.",
     "parameters": {"type": "object", "additionalProperties": False, "properties": {
         "dataset_version_id": _S,
         "category": {"type": "string", "enum": ["statistical_anomaly", "data_quality_issue"]},
         "indicator": {"type": "string", "enum": ["turnout_rate", "leading_party_share"]},
         "severity": {"type": "string", "enum": SEVERITIES}, "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
         "review_status": {"type": "string", "enum": DECISIONS},
         "state": _S, "lga": _S, "ward": _S, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_FINDINGS}}}},
    {"name": "get_finding", "fn": get_finding,
     "description": "One finding in full: explanation, statistics, peer group, the source row's original cells, review history and claims.",
     "parameters": {"type": "object", "required": ["finding_id"], "additionalProperties": False, "properties": {"finding_id": _S}}},
    {"name": "claims_overview", "fn": claims_overview,
     "description": "Counts of claims by status and the latest claims. unverified_allegation is not a fact.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "validation_issues", "fn": validation_issues,
     "description": "Data check results for a dataset version: counts by rule, what each rule means, and example issues with the original cell.",
     "parameters": {"type": "object", "required": ["dataset_version_id"], "additionalProperties": False,
                    "properties": {"dataset_version_id": _S, "rule_id": _S, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}}},
]  # fmt: skip
BY_NAME = {s["name"]: s for s in SPECS}


def openai_tools() -> list[dict[str, Any]]:
    return [{"type": "function", "function": {k: s[k] for k in ("name", "description", "parameters")}} for s in SPECS]
