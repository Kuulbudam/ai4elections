"""Deterministic analysis engine, method version 1.0.0 (master prompt 5.6). Pure: no I/O.

Input: the canonical records of one validated dataset version (with their row status and scoped
exclusions) and that version's validation issues. Output: findings (statistical anomalies and
data quality issues), the list of units that were not scored and why, a summary, and a content
hash over all of it.

Method, per indicator:
* turnout_rate = accredited / registered (not computed when registered = 0)
* leading_party_share = highest party votes / valid votes (not computed when valid = 0)
* Peer group = the eligible units in the same ward (state + LGA + ward). If fewer than
  MIN_PEERS, the same LGA is used instead and the finding says so. If the LGA is also too small,
  the unit is "not scored - insufficient peers". The unit itself is part of its peer group.
* median, MAD = median(|x - median|), robust_z = 0.6745 * (x - median) / MAD.
  MAD = 0 means "not scored - zero MAD"; no score is invented.
* Flag when |robust_z| >= 3.5. Severity by |z|: [3.5, 5) medium, [5, 7) high, >= 7 very high.
* Confidence: low if the row has validation warnings; otherwise high for a ward group of 20 or
  more, medium for 10-19 units or an LGA fallback. Severity and confidence are never combined.

Determinism: plain float arithmetic in a fixed order, values rounded before they are compared or
stored, output sorted by fixed keys, and the hash taken over canonical JSON. Same input, same
findings, same order, same hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

ENGINE_VERSION = "1.0.0"
METHOD_VERSION = "1.0.0"
TURNOUT = "turnout_rate"
SHARE = "leading_party_share"
INDICATORS = (TURNOUT, SHARE)
PARAMETERS: dict[str, Any] = {
    "min_peers": 10,
    "threshold": 3.5,
    "consistency_constant": 0.6745,
    "severity_bands": {"medium": 3.5, "high": 5.0, "very_high": 7.0},
    "confidence_high_min_n": 20,
    "unit_included_in_own_peer_group": True,
    "peer_levels": ["ward", "lga"],
}
INDICATOR_LABEL = {TURNOUT: "Turnout", SHARE: "Leading-party vote share"}

# Plain names for data quality findings (rule texts live in the validation rule catalogue).
RULE_TITLES = {
    "V003": "Missing value",
    "V004": "Count is not a whole number",
    "V005": "Negative count",
    "V006": "Accredited voters exceed registered voters",
    "V007": "Votes do not reconcile",
    "V008": "Party votes do not match valid votes",
    "V009": "A count exceeds the total it belongs to",
    "V010": "Duplicate polling unit code",
    "V011": "Ward not in the reference list",
    "V012": "Invalid coordinates",
    "V013": "All party scores are round numbers",
    "V014": "Same party scores as another polling unit",
}

STAT_LIMITATIONS = (
    "A statistical outlier is a reason to check the result sheet, not evidence of wrongdoing. "
    "Ordinary explanations include a reading or transcription error, a very small or very large polling unit, "
    "local turnout patterns, or a candidate's home area. Peer groups are administrative areas (ward, or LGA when "
    "the ward is too small), so neighbouring units across a boundary are not compared. The unit itself is "
    "included in its peer group's median. Only the parties present in the file are counted."
)
PATTERN_RULES = {"V013", "V014"}
DQ_LIMITATIONS = (
    "This is a data check on the figures as received, not a statistical finding. The cause may be a reading or "
    "transcription error, a damaged or unreadable result sheet, or a problem in the source file. Check the "
    "original result sheet before drawing any conclusion."
)


@dataclass
class Unit:
    raw_row_id: str
    source_row_number: int
    pu_code: str | None
    state: str | None
    lga: str | None
    ward: str | None
    registered_voters: int | None
    accredited_voters: int | None
    valid_votes: int | None
    party_votes: dict[str, int | None]
    row_status: str = "eligible"
    excluded_from: list[str] = field(default_factory=list)
    exclusion_reasons: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AnalysisResult:
    findings: list[dict[str, Any]]
    not_scored: list[dict[str, Any]]
    summary: dict[str, Any]
    content_hash: str


def norm(s: str | None) -> str:
    return " ".join((s or "").split()).casefold()


def median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        raise ValueError("median of empty list")
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def mad(xs: list[float], med: float | None = None) -> float:
    m = median(xs) if med is None else med
    return median([abs(x - m) for x in xs])


def robust_z(x: float, med: float, mad_value: float) -> float | None:
    """None when MAD is zero: no score is computed."""
    if mad_value == 0:
        return None
    return PARAMETERS["consistency_constant"] * (x - med) / mad_value


def severity_for(z: float) -> str | None:
    a, b = abs(z), PARAMETERS["severity_bands"]
    if a >= b["very_high"]:
        return "very_high"
    if a >= b["high"]:
        return "high"
    if a >= b["medium"]:
        return "medium"
    return None


def confidence_for(row_status: str, level: str, n: int) -> str:
    if row_status == "eligible_with_warnings":
        return "low"
    if level == "ward" and n >= PARAMETERS["confidence_high_min_n"]:
        return "high"
    return "medium"


def _r(x: float, nd: int = 6) -> float:
    return round(x, nd)


def indicator_value(u: Unit, ind: str) -> tuple[float | None, dict[str, Any] | None, dict[str, Any]]:
    """(value, not-scored reason or None, inputs). Excluded units and uncomputable values give None."""
    if "all" in u.excluded_from or ind in u.excluded_from:
        rules = sorted({e.get("rule_id") for e in u.exclusion_reasons if e.get("rule_id")})
        return None, {"reason": "excluded_by_validation", "rules": rules}, {}
    if not (u.state and u.lga and u.ward):
        return None, {"reason": "missing_geography"}, {}
    if ind == TURNOUT:
        reg, acc = u.registered_voters, u.accredited_voters
        if reg is None or acc is None:
            return None, {"reason": "not_computable", "detail": "missing count"}, {}
        if reg == 0:
            return None, {"reason": "not_computable", "detail": "registered voters is 0"}, {}
        return _r(acc / reg), None, {"accredited_voters": acc, "registered_voters": reg}
    val = u.valid_votes
    votes = u.party_votes
    if val is None or any(v is None for v in votes.values()) or not votes:
        return None, {"reason": "not_computable", "detail": "missing count"}, {}
    if val == 0:
        return None, {"reason": "not_computable", "detail": "valid votes is 0"}, {}
    top = max(votes.values())
    leaders = sorted(k for k, v in votes.items() if v == top)
    return _r(top / val), None, {"leading_party": leaders[0], "tied_leaders": leaders, "leading_votes": top,
                                 "valid_votes": val}  # fmt: skip


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _stat_finding(u: Unit, ind: str, value: float, inputs: dict, level: str, vals: list[float],
                  med: float, mad_v: float, z: float, excluded: list[dict]) -> dict[str, Any]:  # fmt: skip
    sev = severity_for(z)
    conf = confidence_for(u.row_status, level, len(vals))
    direction = "above" if z > 0 else "below"
    where = f"ward {u.ward}" if level == "ward" else f"LGA {u.lga} (ward too small)"
    if ind == TURNOUT:
        what = (f"Turnout is {_pct(value)} ({inputs['accredited_voters']} accredited of "
                f"{inputs['registered_voters']} registered).")  # fmt: skip
    else:
        what = (f"The leading party ({inputs['leading_party']}) has {_pct(value)} of valid votes "
                f"({inputs['leading_votes']} of {inputs['valid_votes']}).")  # fmt: skip
    explanation = (
        f"{what} The median for the {len(vals)} polling units in {where} is {_pct(med)}. "
        f"Robust z = {z:+.2f}, beyond the {PARAMETERS['threshold']} threshold, so this unit is unusually "
        f"{direction} its peers."
    )
    limitations = STAT_LIMITATIONS + (
        " The ward had too few comparable units, so the whole LGA was used." if level == "lga" else ""
    )
    return {
        "category": "statistical_anomaly",
        "indicator": ind,
        "rule_id": None,
        "raw_row_id": u.raw_row_id,
        "pu_code": u.pu_code,
        "state": u.state,
        "lga": u.lga,
        "ward": u.ward,
        "severity": sev,
        "confidence": conf,
        "title": f"{INDICATOR_LABEL[ind]} unusually {direction} peers at polling unit {u.pu_code}",
        "explanation": explanation,
        "limitations": limitations,
        "peer_group": {
            "level": level,
            "key": {"state": u.state, "lga": u.lga, **({"ward": u.ward} if level == "ward" else {})},
            "n": len(vals),
            "fallback_used": level == "lga",
            "unit_included": True,
        },
        "statistics": {
            "value": value,
            "median": _r(med),
            "mad": _r(mad_v),
            "robust_z": z,
            "threshold": PARAMETERS["threshold"],
            "direction": direction,
            "peer_min": min(vals),
            "peer_max": max(vals),
            **({"leading_party": inputs["leading_party"]} if ind == SHARE else {}),
        },
        "exclusions": excluded,
        "evidence": [_evidence(u, ind, value, inputs)],
        "source_row_number": u.source_row_number,
    }


def _evidence(u: Unit, ind: str | None, value: float | None, inputs: dict | None) -> dict[str, Any]:
    return {
        "raw_row_id": u.raw_row_id,
        "source_row_number": u.source_row_number,
        "pu_code": u.pu_code,
        "values": {
            "registered_voters": u.registered_voters,
            "accredited_voters": u.accredited_voters,
            "valid_votes": u.valid_votes,
            "party_votes": dict(sorted(u.party_votes.items())),
        },
        **({"indicator": ind, "indicator_value": value, "inputs": inputs} if ind else {}),
    }


def _dq_findings(units: dict[str, Unit], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], list[dict]] = {}
    for i in issues:
        if i.get("raw_row_id") and i["raw_row_id"] in units:
            by_key.setdefault((i["raw_row_id"], i["rule_id"]), []).append(i)
    out = []
    for (rid, rule), items in by_key.items():
        u = units[rid]
        items = sorted(items, key=lambda i: (i.get("source_column") or "", i.get("message") or ""))
        # Pattern warnings (round numbers, copied figures) matter as much as an arithmetic error.
        sev = "medium" if any(i["severity"] == "error" for i in items) or rule in PATTERN_RULES else "low"
        where = u.pu_code or f"(row {u.source_row_number})"
        title = f"{RULE_TITLES.get(rule, 'Data check failed')} at polling unit {where}"
        out.append({
            "category": "data_quality_issue",
            "indicator": None,
            "rule_id": rule,
            "raw_row_id": rid,
            "pu_code": u.pu_code,
            "state": u.state,
            "lga": u.lga,
            "ward": u.ward,
            "severity": sev,
            "confidence": "high",
            "title": title,
            "explanation": " ".join(i["message"] for i in items),
            "limitations": DQ_LIMITATIONS,
            "peer_group": None,
            "statistics": None,
            "exclusions": [],
            "evidence": [
                _evidence(u, None, None, None)
                | {"cells": [{"column": i.get("source_column"), "original_value": i.get("original_value"),
                              "rule_id": rule, "message": i["message"]} for i in items]}  # fmt: skip
            ],
            "source_row_number": u.source_row_number,
        })  # fmt: skip
    return out


def analyse(records: list[Unit], issues: list[dict[str, Any]] | None = None) -> AnalysisResult:
    units = sorted(records, key=lambda u: (u.source_row_number, u.raw_row_id))
    by_id = {u.raw_row_id: u for u in units}
    findings: list[dict[str, Any]] = []
    not_scored: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"units": len(units), "indicators": {}}

    value: dict[str, dict[str, float]] = {i: {} for i in INDICATORS}
    inputs_of: dict[str, dict[str, dict]] = {i: {} for i in INDICATORS}
    why_of: dict[str, dict[str, dict]] = {i: {} for i in INDICATORS}

    for ind in INDICATORS:
        ward_groups: dict[tuple, list[Unit]] = {}
        lga_groups: dict[tuple, list[Unit]] = {}
        ward_excl: dict[tuple, list[dict]] = {}
        lga_excl: dict[tuple, list[dict]] = {}
        for u in units:
            v, why, inputs = indicator_value(u, ind)
            wk, lk = (norm(u.state), norm(u.lga), norm(u.ward)), (norm(u.state), norm(u.lga))
            if v is None:
                why_of[ind][u.raw_row_id] = why or {}
                ex = {"raw_row_id": u.raw_row_id, "pu_code": u.pu_code, **(why or {})}
                ward_excl.setdefault(wk, []).append(ex)
                lga_excl.setdefault(lk, []).append(ex)
                continue
            value[ind][u.raw_row_id], inputs_of[ind][u.raw_row_id] = v, inputs
            ward_groups.setdefault(wk, []).append(u)
            lga_groups.setdefault(lk, []).append(u)

        stats_cache: dict[tuple, tuple[float, float]] = {}
        s = {"eligible": 0, "scored": 0, "flagged": 0, "lga_fallback": 0, "not_scored": {},
             "flagged_by_severity": {"medium": 0, "high": 0, "very_high": 0}}  # fmt: skip
        for u in units:
            if u.raw_row_id not in value[ind]:
                why = why_of[ind][u.raw_row_id]
                not_scored.append({"indicator": ind, "raw_row_id": u.raw_row_id, "pu_code": u.pu_code,
                                   "source_row_number": u.source_row_number, **why})  # fmt: skip
                s["not_scored"][why["reason"]] = s["not_scored"].get(why["reason"], 0) + 1
                continue
            s["eligible"] += 1
            wk, lk = (norm(u.state), norm(u.lga), norm(u.ward)), (norm(u.state), norm(u.lga))
            if len(ward_groups[wk]) >= PARAMETERS["min_peers"]:
                level, key, group, excl = "ward", ("w", *wk), ward_groups[wk], ward_excl.get(wk, [])
            elif len(lga_groups[lk]) >= PARAMETERS["min_peers"]:
                level, key, group, excl = "lga", ("l", *lk), lga_groups[lk], lga_excl.get(lk, [])
            else:
                why = {"reason": "insufficient_peers", "ward_n": len(ward_groups[wk]), "lga_n": len(lga_groups[lk])}
                not_scored.append({"indicator": ind, "raw_row_id": u.raw_row_id, "pu_code": u.pu_code,
                                   "source_row_number": u.source_row_number, **why})  # fmt: skip
                s["not_scored"]["insufficient_peers"] = s["not_scored"].get("insufficient_peers", 0) + 1
                continue
            if key not in stats_cache:
                xs = [value[ind][g.raw_row_id] for g in group]
                m = median(xs)
                stats_cache[key] = (m, mad(xs, m))
            med, mad_v = stats_cache[key]
            x = value[ind][u.raw_row_id]
            z = robust_z(x, med, mad_v)
            if z is None:
                why = {"reason": "zero_mad", "level": level, "n": len(group), "median": _r(med)}
                not_scored.append({"indicator": ind, "raw_row_id": u.raw_row_id, "pu_code": u.pu_code,
                                   "source_row_number": u.source_row_number, **why})  # fmt: skip
                s["not_scored"]["zero_mad"] = s["not_scored"].get("zero_mad", 0) + 1
                continue
            z = round(z, 4)
            s["scored"] += 1
            s["lga_fallback"] += level == "lga"
            if abs(z) >= PARAMETERS["threshold"]:
                vals = sorted(value[ind][g.raw_row_id] for g in group)
                excl_sorted = sorted(excl, key=lambda e: (e.get("pu_code") or "", e["raw_row_id"]))
                f = _stat_finding(u, ind, x, inputs_of[ind][u.raw_row_id], level, vals, med, mad_v, z, excl_sorted)
                findings.append(f)
                s["flagged"] += 1
                s["flagged_by_severity"][f["severity"]] += 1
        s["not_scored"] = dict(sorted(s["not_scored"].items()))
        summary["indicators"][ind] = s

    dq = _dq_findings(by_id, issues or [])
    findings.extend(dq)
    summary["data_quality_findings"] = len(dq)
    summary["data_quality_by_rule"] = dict(sorted(_count(f["rule_id"] for f in dq).items()))
    summary["statistical_findings"] = sum(1 for f in findings if f["category"] == "statistical_anomaly")

    order = {"statistical_anomaly": 0, "data_quality_issue": 1}
    findings.sort(key=lambda f: (order[f["category"]], f["indicator"] or "", f["rule_id"] or "",
                                 f["source_row_number"], f["raw_row_id"]))  # fmt: skip
    not_scored.sort(key=lambda n: (n["indicator"], n["source_row_number"], n["raw_row_id"]))

    payload = {
        "engine_version": ENGINE_VERSION,
        "method_version": METHOD_VERSION,
        "parameters": PARAMETERS,
        "findings": findings,
        "not_scored": not_scored,
    }
    digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
    return AnalysisResult(findings, not_scored, summary, digest)


def _count(xs: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
