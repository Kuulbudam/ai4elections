# ruff: noqa: E501  (report text and table templates are kept on one line each)
"""Report snapshots (master prompt 5.9, ADR-0005, ADR-0011). No AI is used anywhere in this module.

build_body() reads one analysis run and everything a reader needs to check it: project, dataset
version and provenance, analysis versions and parameters, methodology, validation summary, every
finding with evidence, review status and history, claims (labelled), and limitations.

content_hash() = SHA-256 of canonical JSON (sorted keys, no spaces, rates as fixed-decimal strings)
of the body. The body never contains the report id, generation time, generating user or the audit
checkpoint (all stored next to it), so regenerating from the same inputs gives the same hash.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any

import psycopg

REPORT_FORMAT = "ai4e-report/1"

METHODOLOGY = (
    "Validation: every row of the uploaded file is checked with versioned rules (missing or non-numeric values, "
    "accredited above registered, votes that do not reconcile, party votes against valid votes, duplicate polling-unit "
    "codes, labels outside the reference list, invalid coordinates). A row is only left out of the calculations its "
    "problem affects. Analysis: for each polling unit, turnout (accredited / registered) and leading-party share "
    "(highest party votes / valid votes) are compared with the other units in the same ward, or the same LGA when the "
    "ward has fewer than 10 usable units. The comparison uses the median and the median absolute deviation (MAD); a "
    "unit is listed when its robust z-score is 3.5 or more away from zero. Units with too few peers, or whose peers all "
    "have the same value, are not scored. Severity says how far from its peers a unit is (medium 3.5-5, high 5-7, very "
    "high 7+); confidence says how much the comparison can be trusted (peer-group size and data warnings). The same "
    "data always gives the same results and the same hash."
)
LIMITATIONS = (
    "Statistical anomalies are not evidence of fraud. A finding means a figure is unusual compared with similar "
    "polling units, or that a data check failed; it is a reason to examine the original result sheet. Ordinary causes "
    "include reading or transcription errors, damaged or unreadable sheets, very small or very large polling units, "
    "local turnout patterns and home-area voting. Results depend on the data as uploaded; units excluded by data checks "
    "are not scored. Allegations recorded by users are unverified unless marked as verified facts after independent "
    "corroboration and a second person's sign-off."
)


def _dec(x: Any, nd: int) -> str | None:
    return None if x is None else f"{float(x):.{nd}f}"


def build_body(db: psycopg.Connection, run_id: str) -> dict[str, Any]:
    run = db.execute(
        """SELECT r.id, r.org_id, r.project_id, r.dataset_version_id, r.engine_version, r.method_version,
                  r.parameters, r.rule_versions, r.content_hash, r.completed_at, r.summary,
                  p.election_name, p.election_year, p.country, p.election_level,
                  v.version_number, v.original_filename, v.sha256, v.declared_size_bytes, v.row_count, v.created_at,
                  v.validation_summary, v.mapping, d.name AS dataset_name,
                  s.source_type, s.description AS source_description
           FROM public.analytical_runs r
           JOIN public.projects p ON p.id = r.project_id
           JOIN public.dataset_versions v ON v.id = r.dataset_version_id
           JOIN public.datasets d ON d.id = v.dataset_id
           JOIN public.data_sources s ON s.id = v.data_source_id
           WHERE r.id = %s AND r.status = 'complete'""",
        (run_id,),
    ).fetchone()
    if run is None:
        raise LookupError("analysis run not found or not complete")
    findings = db.execute(
        """SELECT f.id::text AS id, f.category, f.indicator, f.rule_id, f.pu_code, f.state, f.lga, f.ward,
                  f.severity, f.confidence, f.title, f.explanation, f.statistics, f.peer_group, f.evidence,
                  w.source_row_number
           FROM public.findings f LEFT JOIN public.raw_rows w ON w.id = f.raw_row_id
           WHERE (f.analytical_run_id = %s OR (f.analytical_run_id IS NULL AND f.category = 'cross_check' AND f.dataset_version_id = (SELECT dataset_version_id FROM public.analytical_runs WHERE id = %s))) ORDER BY f.id""",  # noqa: E501
        (run_id, run_id),
    ).fetchall()
    reviews = db.execute(
        """SELECT fr.finding_id::text AS finding_id, fr.decision, fr.reason, fr.created_at,
                  coalesce(nullif(pr.display_name, ''), pr.email) AS reviewer
           FROM public.finding_reviews fr JOIN public.findings f ON f.id = fr.finding_id
           LEFT JOIN public.profiles pr ON pr.id = fr.reviewer_id
           WHERE (f.analytical_run_id = %s OR (f.analytical_run_id IS NULL AND f.category = 'cross_check' AND f.dataset_version_id = (SELECT dataset_version_id FROM public.analytical_runs WHERE id = %s))) ORDER BY fr.finding_id, fr.created_at, fr.id""",  # noqa: E501
        (run_id, run_id),
    ).fetchall()
    claims = db.execute(
        """SELECT c.id::text AS id, c.finding_id::text AS finding_id, c.statement, c.status, c.created_at,
                  c.verified_at, c.verification_reason,
                  coalesce(nullif(pc.display_name, ''), pc.email) AS recorded_by,
                  coalesce(nullif(pv.display_name, ''), pv.email) AS verified_by
           FROM public.claims c
           LEFT JOIN public.profiles pc ON pc.id = c.created_by LEFT JOIN public.profiles pv ON pv.id = c.verified_by
           WHERE c.project_id = %s
             AND (c.finding_id IS NULL OR c.finding_id IN (SELECT f.id FROM public.findings f WHERE (f.analytical_run_id = %s OR (f.analytical_run_id IS NULL AND f.category = 'cross_check' AND f.dataset_version_id = (SELECT dataset_version_id FROM public.analytical_runs WHERE id = %s)))))
           ORDER BY c.id""",  # noqa: E501
        (str(run["project_id"]), run_id, run_id),
    ).fetchall()
    evidence = db.execute(
        """SELECT e.claim_id::text AS claim_id, e.kind, e.description, e.url, e.file_sha256
           FROM public.claim_evidence e JOIN public.claims c ON c.id = e.claim_id
           WHERE c.project_id = %s ORDER BY e.claim_id, e.created_at, e.id""",
        (str(run["project_id"]),),
    ).fetchall()

    hist: dict[str, list[dict[str, Any]]] = {}
    for r in reviews:
        hist.setdefault(r["finding_id"], []).append(
            {
                "decision": r["decision"],
                "reason": r["reason"],
                "reviewer": r["reviewer"],
                "at": r["created_at"].isoformat(),
            }  # fmt: skip
        )
    ev_by: dict[str, list[dict[str, Any]]] = {}
    for e in evidence:
        ev_by.setdefault(e["claim_id"], []).append(
            {"kind": e["kind"], "description": e["description"], "url": e["url"], "file_sha256": e["file_sha256"]}
        )

    out_findings = []
    for f in findings:
        st, pg = f["statistics"] or {}, f["peer_group"] or {}
        ev0 = (f["evidence"] or [{}])[0]
        h = hist.get(f["id"], [])
        out_findings.append({
            "finding_id": f["id"], "category": f["category"], "indicator": f["indicator"], "rule_id": f["rule_id"],
            "pu_code": f["pu_code"], "state": f["state"], "lga": f["lga"], "ward": f["ward"],
            "severity": f["severity"], "confidence": f["confidence"], "title": f["title"],
            "explanation": f["explanation"],
            "statistics": {"value": _dec(st.get("value"), 6), "median": _dec(st.get("median"), 6),
                           "mad": _dec(st.get("mad"), 6), "robust_z": _dec(st.get("robust_z"), 4),
                           "threshold": _dec(st.get("threshold"), 1), "leading_party": st.get("leading_party")}
            if st else None,
            "peer_group": {"level": pg.get("level"), "n": pg.get("n"), "fallback_used": pg.get("fallback_used")}
            if pg else None,
            "evidence": {"source_row_number": f["source_row_number"], "values": ev0.get("values"),
                         "cells": ev0.get("cells")},
            "review_status": h[-1]["decision"] if h else "unreviewed",
            "review_history": h,
        })  # fmt: skip

    vs = run["validation_summary"] or {}
    return {
        "format": REPORT_FORMAT,
        "project": {"election_name": run["election_name"], "election_year": run["election_year"],
                    "country": run["country"], "election_level": run["election_level"]},
        "dataset": {"name": run["dataset_name"], "version": run["version_number"], "file": run["original_filename"],
                    "sha256": run["sha256"], "size_bytes": run["declared_size_bytes"], "rows": run["row_count"],
                    "uploaded_at": run["created_at"].isoformat(), "source_type": run["source_type"],
                    "source_description": run["source_description"],
                    "mapping_schema_version": (run["mapping"] or {}).get("schema_version")},
        "analysis": {"run_id": str(run["id"]), "engine_version": run["engine_version"],
                     "method_version": run["method_version"], "rule_versions": run["rule_versions"],
                     "parameters": run["parameters"], "run_content_hash": run["content_hash"],
                     "completed_at": run["completed_at"].isoformat(), "summary": run["summary"]},
        "methodology": METHODOLOGY,
        "validation": {"rows": vs.get("rows"), "row_status": vs.get("row_status"),
                       "issues_by_rule": vs.get("issues_by_rule"), "rows_by_rule": vs.get("rows_by_rule"),
                       "ruleset_version": vs.get("ruleset_version"), "reference_list_used": vs.get("reference_list_used")},
        "findings": out_findings,
        "claims": [{"claim_id": c["id"], "finding_id": c["finding_id"], "statement": c["statement"],
                    "status": c["status"],
                    "label": "VERIFIED FACT" if c["status"] == "verified_fact" else "UNVERIFIED ALLEGATION",
                    "recorded_by": c["recorded_by"], "recorded_at": c["created_at"].isoformat(),
                    "verified_by": c["verified_by"],
                    "verified_at": c["verified_at"].isoformat() if c["verified_at"] else None,
                    "verification_reason": c["verification_reason"], "evidence": ev_by.get(c["id"], [])}
                   for c in claims],
        "limitations": LIMITATIONS,
    }  # fmt: skip


def canonical(body: dict[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)


def content_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ exports (formula-injection safe)

_DANGER = ("=", "+", "-", "@", "\t", "\r")


def safe_text(v: Any) -> Any:
    """Numbers stay numbers; text that a spreadsheet could run as a formula gets a leading quote."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return v
    s = str(v)
    return "'" + s if s.startswith(_DANGER) else s


def _num(s: Any) -> Any:
    try:
        return float(s) if s is not None and s != "" else None
    except (TypeError, ValueError):
        return s


CSV_COLUMNS = ["finding_id", "category", "indicator", "rule_id", "pu_code", "state", "lga", "ward", "severity",
               "confidence", "value", "peer_median", "robust_z", "peers", "source_row", "review_status",
               "last_review_reason", "title"]  # fmt: skip


def to_csv(meta: dict[str, Any], body: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for k, v in (("report", meta["title"]), ("content_hash", meta["content_hash"]),
                 ("dataset_sha256", body["dataset"]["sha256"]), ("generated_at", meta["generated_at"]),
                 ("note", "Statistical anomalies are not evidence of fraud. See the report for methodology and limitations.")):  # fmt: skip
        w.writerow([f"# {k}", safe_text(v)])
    w.writerow(CSV_COLUMNS)
    for f in body["findings"]:
        st = f["statistics"] or {}
        last = f["review_history"][-1]["reason"] if f["review_history"] else ""
        row = [f["finding_id"], f["category"], f["indicator"], f["rule_id"], f["pu_code"], f["state"], f["lga"],
               f["ward"], f["severity"], f["confidence"], _num(st.get("value")), _num(st.get("median")),
               _num(st.get("robust_z")), (f["peer_group"] or {}).get("n"), f["evidence"]["source_row_number"],
               f["review_status"], last, f["title"]]  # fmt: skip
        w.writerow([safe_text(x) for x in row])
    return buf.getvalue()


def _md(v: Any) -> str:
    s = safe_text(v)
    s = str(s).replace("\r", " ").replace("\n", " ")
    return s.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def to_markdown(meta: dict[str, Any], body: dict[str, Any]) -> str:
    p, d, a, v = body["project"], body["dataset"], body["analysis"], body["validation"]
    stat = [f for f in body["findings"] if f["category"] == "statistical_anomaly"]
    dq = [f for f in body["findings"] if f["category"] == "data_quality_issue"]
    xc = [f for f in body["findings"] if f["category"] == "cross_check"]
    sev = {"very_high": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    stat.sort(
        key=lambda f: (sev[f["severity"]], -abs(float((f["statistics"] or {}).get("robust_z") or 0)), f["finding_id"])
    )
    L: list[str] = [
        f"# {_md(meta['title'])}", "",
        f"**{_md(p['election_name'])}** ({_md(p['election_year'])}, {_md(p['country'])}, {_md(p['election_level'])})", "",
        f"- Generated: {_md(meta['generated_at'])} by {_md(meta['generated_by'])}",
        f"- Report content hash: `{meta['content_hash']}`",
        f"- Audit checkpoint: event `{_md(meta['audit_event_id'])}`, hash `{_md(meta['audit_event_hash'])}`", "",
        "> " + _md(body["limitations"]), "",
        "## Dataset and provenance", "",
        f"- Dataset: {_md(d['name'])}, version {d['version']} (`{_md(d['file'])}`), {d['rows']} rows",
        f"- SHA-256: `{d['sha256']}`",
        f"- Source: {_md(d['source_type'])}: {_md(d['source_description'])}", "",
        "## Analysis", "",
        f"- Run `{a['run_id']}`, engine {_md(a['engine_version'])}, method {_md(a['method_version'])}, "
        f"rules {_md((a['rule_versions'] or {}).get('ruleset'))}",
        f"- Parameters: minimum peers {a['parameters'].get('min_peers')}, threshold |z| ≥ {a['parameters'].get('threshold')}",
        f"- Run content hash: `{a['run_content_hash']}`", "",
        "## Methodology", "", _md(body["methodology"]), "",
        "## Validation summary", "",
        f"{v['rows']} rows checked (rule set {_md(v['ruleset_version'])}). Row status: "
        + ", ".join(f"{_md(k)} {n}" for k, n in (v["row_status"] or {}).items()) + ".", "",
        "| Rule | Issues | Rows |", "|---|---|---|",
        *[f"| {_md(r)} | {n} | {(v['rows_by_rule'] or {}).get(r, '')} |" for r, n in (v["issues_by_rule"] or {}).items()], "",
        f"## Statistical outliers ({len(stat)})", "",
        "| Polling unit | LGA / ward | Indicator | Value | Peer median | Robust z | Peers | Severity | Confidence | Review | Source row |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    for f in stat:
        st, pg = f["statistics"] or {}, f["peer_group"] or {}
        pct = lambda x: "" if x is None else f"{float(x) * 100:.1f}%"  # noqa: E731
        L.append(f"| {_md(f['pu_code'])} | {_md(f['lga'])} / {_md(f['ward'])} | {_md(f['indicator'])} | {pct(st.get('value'))} "
                 f"| {pct(st.get('median'))} | {_md(st.get('robust_z'))} | {pg.get('n', '')}{' (LGA)' if pg.get('fallback_used') else ''} "
                 f"| {_md(f['severity'])} | {_md(f['confidence'])} | {_md(f['review_status'])} | {f['evidence']['source_row_number']} |")  # fmt: skip
    L += ["", f"## Cross-checks: result sheets and collation ({len(xc)})", ""]
    if not xc:
        L.append("No cross-check findings.")
    else:
        L += ["| Polling unit / area | Check | Severity | Review | Detail |", "|---|---|---|---|---|"]
        for f in sorted(xc, key=lambda f: (sev[f["severity"]], f["finding_id"])):
            L.append(f"| {_md(f['pu_code'] or f['ward'] or f['lga'])} | {_md(f['title'])} | {_md(f['severity'])} "
                     f"| {_md(f['review_status'])} | {_md(f['explanation'])} |")  # fmt: skip
    L += ["", f"## Data quality findings ({len(dq)})", "",
          "Listed in full in the CSV export. Count by rule: "
          + ", ".join(f"{_md(r)} {n}" for r, n in sorted(_count(f["rule_id"] for f in dq).items())) + ".", "",
          "## Review decisions", ""]  # fmt: skip
    reviewed = [f for f in body["findings"] if f["review_history"]]
    if not reviewed:
        L.append("No review decisions have been recorded.")
    for f in reviewed:
        L.append(f"**{_md(f['title'])}**: current status *{_md(f['review_status'])}*")
        L += [
            f"- {_md(h['at'])}: {_md(h['decision'])} by {_md(h['reviewer'])}: {_md(h['reason'])}"
            for h in f["review_history"]
        ]
        L.append("")
    L += ["", "## Claims", ""]
    if not body["claims"]:
        L.append("No claims have been recorded.")
    for c in body["claims"]:
        L.append(f"- **{c['label']}**: {_md(c['statement'])} (recorded by {_md(c['recorded_by'])}"
                 + (f"; verified by {_md(c['verified_by'])}: {_md(c['verification_reason'])}" if c["verified_by"] else "")
                 + f"; {len(c['evidence'])} evidence item(s))")  # fmt: skip
    L += ["", "## Limitations", "", _md(body["limitations"]), ""]
    return "\n".join(L)


def _count(xs: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in xs:
        out[str(x)] = out.get(str(x), 0) + 1
    return out
