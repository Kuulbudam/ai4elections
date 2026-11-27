"""Analysis engine: hand-checked statistics, not-scored cases, ground-truth scoring, determinism."""

from __future__ import annotations

import csv
import re
from collections import Counter

import pytest

from app.analysis.engine import (
    PARAMETERS,
    Unit,
    analyse,
    confidence_for,
    mad,
    median,
    robust_z,
    severity_for,
)
from app.ingest.csv_parse import parse_csv
from app.validation.engine import validate

from .test_validation_engine import DATA, MAPPING

# ------------------------------------------------------------------ hand-checked arithmetic

XS = [0.30, 0.32, 0.34, 0.36, 0.38, 0.40, 0.42, 0.44, 0.46, 0.90]
# sorted already; median = (0.38 + 0.40) / 2 = 0.39
# |x - 0.39| = .09 .07 .05 .03 .01 .01 .03 .05 .07 .51 → sorted .01 .01 .03 .03 .05 .05 .07 .07 .09 .51
# MAD = (.05 + .05) / 2 = 0.05
# robust_z(0.90) = 0.6745 × 0.51 / 0.05 = 6.8799 → "high" (5 ≤ |z| < 7)


def test_median_odd_even():
    assert median([3, 1, 2]) == 2
    assert median([4, 1, 3, 2]) == 2.5
    with pytest.raises(ValueError):
        median([])


def test_hand_checked_example():
    m = median(XS)
    assert m == pytest.approx(0.39)
    assert mad(XS) == pytest.approx(0.05)
    z = robust_z(0.90, m, mad(XS))
    assert z == pytest.approx(6.8799, abs=1e-4)
    assert severity_for(z) == "high"
    assert robust_z(0.30, m, mad(XS)) == pytest.approx(-1.2141, abs=1e-4)
    assert severity_for(-1.2141) is None


def test_zero_mad_gives_no_score():
    assert robust_z(0.5, 0.4, 0.0) is None


@pytest.mark.parametrize(
    "z,sev", [(3.49, None), (3.5, "medium"), (-4.99, "medium"), (5.0, "high"), (-6.99, "high"), (7.0, "very_high")]
)
def test_severity_bands(z, sev):
    assert severity_for(z) == sev


@pytest.mark.parametrize(
    "status,level,n,conf",
    [
        ("eligible", "ward", 20, "high"),
        ("eligible", "ward", 19, "medium"),
        ("eligible", "ward", 10, "medium"),
        ("eligible", "lga", 50, "medium"),
        ("eligible_with_warnings", "ward", 40, "low"),
    ],
)
def test_confidence(status, level, n, conf):
    assert confidence_for(status, level, n) == conf


# ------------------------------------------------------------------ small synthetic groups


def unit(i, ward="W1", lga="L1", reg=100, acc=40, valid=40, votes=None, status="eligible", excl=()):
    return Unit(
        raw_row_id=f"r{i:05d}", source_row_number=i + 1, pu_code=f"PU{i}", state="S", lga=lga, ward=ward,
        registered_voters=reg, accredited_voters=acc, valid_votes=valid,
        party_votes=votes if votes is not None else {"A": 25, "B": 15},
        row_status=status, excluded_from=list(excl),
        exclusion_reasons=[{"rule_id": "V007", "column": "x"}] if excl else [],
    )  # fmt: skip


def turnout_ward(accs, **kw):
    return [unit(i, acc=a, valid=a, votes={"A": a // 2 + 1, "B": a - a // 2 - 1}, **kw) for i, a in enumerate(accs)]


def test_ward_group_flags_outlier_with_hand_checked_numbers():
    units = turnout_ward([30, 32, 34, 36, 38, 40, 42, 44, 46, 90])
    res = analyse(units)
    t = [f for f in res.findings if f["indicator"] == "turnout_rate"]
    assert len(t) == 1
    f = t[0]
    assert f["pu_code"] == "PU9" and f["severity"] == "high" and f["confidence"] == "medium"  # n = 10
    assert f["statistics"]["median"] == pytest.approx(0.39) and f["statistics"]["mad"] == pytest.approx(0.05)
    assert f["statistics"]["robust_z"] == pytest.approx(6.8799, abs=1e-4)
    assert f["peer_group"] == {
        "level": "ward", "key": {"state": "S", "lga": "L1", "ward": "W1"}, "n": 10,
        "fallback_used": False, "unit_included": True,
    }  # fmt: skip
    assert f["evidence"][0]["raw_row_id"] == "r00009" and f["evidence"][0]["values"]["accredited_voters"] == 90


def test_zero_mad_ward_is_not_scored():
    res = analyse(turnout_ward([40] * 9 + [80]))  # median 40, MAD 0 → no score even for the 80
    reasons = {n["reason"] for n in res.not_scored if n["indicator"] == "turnout_rate"}
    assert reasons == {"zero_mad"}
    assert not [f for f in res.findings if f["indicator"] == "turnout_rate"]


def test_small_ward_falls_back_to_lga_and_says_so():
    small = turnout_ward([30, 31, 95], ward="Tiny")
    big = [unit(i + 10, ward="Big", acc=a, valid=a, votes={"A": a, "B": 0}) for i, a in enumerate(range(30, 45))]
    res = analyse(small + big)
    f = next(f for f in res.findings if f["indicator"] == "turnout_rate" and f["pu_code"] == "PU2")
    assert f["peer_group"]["level"] == "lga" and f["peer_group"]["fallback_used"] is True
    assert f["peer_group"]["n"] == 18 and f["confidence"] == "medium"
    assert "LGA" in f["explanation"] and "too few" in f["limitations"]


def test_insufficient_peers_at_both_levels():
    res = analyse(turnout_ward([30, 35, 40, 45, 90]))
    assert {n["reason"] for n in res.not_scored} == {"insufficient_peers"}
    assert res.findings == []


def test_quarantined_units_are_excluded_and_listed():
    units = turnout_ward([30, 32, 34, 36, 38, 40, 42, 44, 46, 48, 99])
    units[10].excluded_from, units[10].exclusion_reasons = ["turnout_rate"], [{"rule_id": "V006", "column": "c"}]
    units.append(unit(50, acc=10, reg=0))  # zero registered → not computable
    res = analyse(units)
    ns = {(n["pu_code"], n["indicator"]): n for n in res.not_scored}
    assert ns[("PU10", "turnout_rate")]["reason"] == "excluded_by_validation"
    assert ns[("PU10", "turnout_rate")]["rules"] == ["V006"]
    assert ns[("PU50", "turnout_rate")]["reason"] == "not_computable"
    assert ("PU10", "leading_party_share") not in ns  # exclusion is scoped to the affected indicator


def test_warning_rows_get_low_confidence():
    units = turnout_ward([30, 32, 34, 36, 38, 40, 42, 44, 46, 90])
    units[9].row_status = "eligible_with_warnings"
    f = next(f for f in analyse(units).findings if f["indicator"] == "turnout_rate")
    assert f["confidence"] == "low" and f["severity"] == "high"


def test_data_quality_findings_one_per_row_and_rule():
    units = turnout_ward([30, 32])
    issues = [
        {"raw_row_id": "r00000", "rule_id": "V003", "severity": "error", "source_column": "Votes A",
         "original_value": "", "message": "Votes A is blank."},
        {"raw_row_id": "r00000", "rule_id": "V003", "severity": "error", "source_column": "Votes B",
         "original_value": "", "message": "Votes B is blank."},
        {"raw_row_id": "r00001", "rule_id": "V011", "severity": "warning", "source_column": "Ward",
         "original_value": "Wx", "message": "not in the reference list."},
    ]  # fmt: skip
    dq = [f for f in analyse(units, issues).findings if f["category"] == "data_quality_issue"]
    assert [(f["rule_id"], f["severity"], len(f["evidence"][0]["cells"])) for f in dq] == [
        ("V003", "medium", 2),
        ("V011", "low", 1),
    ]


# ------------------------------------------------------------------ the demo dataset vs ground truth


@pytest.fixture(scope="module")
def demo_run():
    parsed = parse_csv((DATA / "SYNTHETIC_results_seed2026.csv").read_bytes())
    ref = [tuple(r) for r in list(csv.reader((DATA / "SYNTHETIC_admin_units_seed2026.csv").open()))[1:]]
    v = validate(parsed.columns, [(f"r{n:05d}", n, c) for n, c in parsed.rows], MAPPING, ref)
    units = [
        Unit(r.raw_row_id, r.source_row_number, r.values["pu_code"], r.values["state"], r.values["lga"],
             r.values["ward"], r.values["registered_voters"], r.values["accredited_voters"], r.values["valid_votes"],
             r.party_votes, r.row_status, r.excluded_from, r.exclusion_reasons)
        for r in v.records
    ]  # fmt: skip
    issues = [i.__dict__ for i in v.issues]
    truth = {
        int(r["source_row_number"]): r for r in csv.DictReader((DATA / "SYNTHETIC_ground_truth_seed2026.csv").open())
    }
    return units, issues, analyse(units, issues), truth


def test_every_planted_anomaly_is_detected(demo_run):
    _, _, res, truth = demo_run
    flagged = {(f["evidence"][0]["source_row_number"], f["indicator"]) for f in res.findings if f["indicator"]}
    expected = {(n, x) for n, t in truth.items() for x in t["expected_flags"].split(";") if x}
    assert len(expected) == 30
    assert expected <= flagged, sorted(expected - flagged)
    sev = Counter(
        f["severity"] for f in res.findings if (f["evidence"][0]["source_row_number"], f["indicator"]) in expected
    )
    assert sev == {"very_high": 30}


def test_false_positives_on_normal_rows_are_measured(demo_run):
    """Recorded in docs/phase-reports/phase-4.md. Fails if the rate drifts, so a change is noticed."""
    _, _, res, truth = demo_run
    normal = {n for n, t in truth.items() if t["scenario"] == "normal"}
    fp = [f for f in res.findings if f["indicator"] and f["evidence"][0]["source_row_number"] in normal]
    assert len(normal) == 11_890  # 3 rows repeat another unit's scores by chance (V014)
    assert Counter(f["severity"] for f in fp) == {"medium": 93, "high": 9}  # 102 of 23,786 unit-indicators (0.43%)
    assert all(f["severity"] != "very_high" for f in fp)


def test_not_scored_cases_match_ground_truth(demo_run):
    _, _, res, truth = demo_run
    reasons: dict[int, set[str]] = {}
    for n in res.not_scored:
        reasons.setdefault(n["source_row_number"], set()).add(n["reason"])
    want = {"zero_mad": "zero_mad", "insufficient_peers": "insufficient_peers", "quarantined": "excluded_by_validation"}
    for n, t in truth.items():
        if t["expected_not_scored"]:
            assert reasons.get(n) == {want[t["expected_not_scored"]]}, (n, t["scenario"], reasons.get(n))
    fallback = {n for n, t in truth.items() if t["scenario"] == "small_ward_lga_fallback"}
    assert not any(n in reasons for n in fallback), "small-ward rows are scored against their LGA"


def test_data_quality_findings_cover_every_validation_issue(demo_run):
    _, issues, res, _ = demo_run
    dq = {(f["raw_row_id"], f["rule_id"]) for f in res.findings if f["category"] == "data_quality_issue"}
    assert dq == {(i["raw_row_id"], i["rule_id"]) for i in issues}
    assert res.summary["data_quality_findings"] == 105  # 79 + 12 round-score + 14 repeated-score rows


def test_two_runs_give_identical_content_hash(demo_run):
    units, issues, res, _ = demo_run
    again = analyse(list(reversed(units)), list(reversed(issues)))
    assert again.content_hash == res.content_hash and again.findings == res.findings
    assert re.fullmatch(r"[0-9a-f]{64}", res.content_hash)


def test_no_finding_uses_forbidden_words(demo_run):
    for f in demo_run[2].findings:
        assert not re.search(r"fraud|rigg", f["title"] + f["explanation"] + f["limitations"], re.I)


def test_parameters_are_versioned_values():
    assert PARAMETERS["min_peers"] == 10 and PARAMETERS["threshold"] == 3.5
