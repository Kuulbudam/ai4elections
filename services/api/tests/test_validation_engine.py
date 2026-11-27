"""Validation engine: unit cases plus exact agreement with the synthetic ground truth."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import pytest

from app.ingest.csv_parse import parse_csv
from app.validation.engine import check_mapping, validate
from app.validation.rules import RULES, definitions

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data/synthetic"

MAPPING = {
    "schema_version": "1.0.0",
    "fields": {
        "pu_code": "PU Code",
        "state": "State",
        "lga": "LGA",
        "ward": "Ward",
        "registered_voters": "Registered Voters",
        "accredited_voters": "Accredited Voters",
        "valid_votes": "Valid Votes",
        "rejected_votes": "Rejected Votes",
        "total_cast": "Total Votes Cast",
        "latitude": "Latitude",
        "longitude": "Longitude",
    },  # fmt: skip
    "party_columns": [{"source_column": f"Votes P{c}", "party_code": f"P{c}"} for c in "ABCDE"],
}


@pytest.fixture(scope="module")
def demo():
    parsed = parse_csv((DATA / "SYNTHETIC_results_seed2026.csv").read_bytes())
    rows = [(f"r{n}", n, cells) for n, cells in parsed.rows]
    with (DATA / "SYNTHETIC_admin_units_seed2026.csv").open() as f:
        units = [(r["state"], r["lga"], r["ward"]) for r in csv.DictReader(f)]
    with (DATA / "SYNTHETIC_ground_truth_seed2026.csv").open() as f:
        truth = {int(r["source_row_number"]): r for r in csv.DictReader(f)}
    return parsed.columns, rows, units, truth


def test_catalogue_is_complete_and_well_formed():
    ids = [d["rule_id"] for d in definitions()]
    assert ids == [f"V{n:03d}" for n in range(1, 15)]
    for d in definitions():
        for key in (
            "rule_id",
            "version",
            "severity",
            "classification",
            "description",
            "affected_fields",
            "remediation",
        ):
            assert d[key], (d["rule_id"], key)
        assert d["severity"] in ("info", "warning", "error")


def test_matches_ground_truth_exactly_for_every_row(demo):
    columns, rows, units, truth = demo
    result = validate(columns, rows, MAPPING, units)
    got: dict[int, set[str]] = {}
    for i in result.issues:
        got.setdefault(i.source_row_number, set()).add(i.rule_id)
    mismatches = []
    for n, t in truth.items():
        expected = set(filter(None, t["expected_rules"].split("|")))
        if got.get(n, set()) != expected:
            mismatches.append((n, t["scenario"], sorted(expected), sorted(got.get(n, set()))))
    assert not mismatches, mismatches[:10]


def test_ground_truth_scores(demo):
    """Per-rule precision and recall against the planted scenarios (all must be 1.0)."""
    columns, rows, units, truth = demo
    result = validate(columns, rows, MAPPING, units)
    flagged = {(i.source_row_number, i.rule_id) for i in result.issues}
    expected = {(n, r) for n, t in truth.items() for r in filter(None, t["expected_rules"].split("|"))}
    tp, fp, fn = len(flagged & expected), len(flagged - expected), len(expected - flagged)
    assert (tp, fp, fn) == (len(expected), 0, 0)
    normal = [n for n, t in truth.items() if t["scenario"] == "normal"]
    assert not any(n in {r for r, _ in flagged} for n in normal), "normal rows must not be flagged"


def test_every_issue_traces_to_source_cell(demo):
    columns, rows, units, _ = demo
    result = validate(columns, rows, MAPPING, units)
    by_no = {n: cells for _, n, cells in rows}
    for i in result.issues:
        assert i.raw_row_id and i.source_row_number in by_no and i.source_column
        if i.rule_id in ("V004", "V005"):
            assert i.original_value == by_no[i.source_row_number][columns.index(i.source_column)]


def test_quarantine_is_scoped(demo):
    columns, rows, units, truth = demo
    result = validate(columns, rows, MAPPING, units)
    by_no = {r.source_row_number: r for r in result.records}
    scen = {n: t["scenario"] for n, t in truth.items()}
    for n, r in by_no.items():
        s = scen[n]
        if s == "normal":
            assert r.row_status == "eligible" and r.excluded_from == []
        if s == "accredited_exceeds_registered":
            assert r.excluded_from == ["turnout_rate"]
        if s in ("party_sum_mismatch", "count_exceeds_denominator", "reconciliation_error"):
            assert r.excluded_from == ["leading_party_share"]
        if s in ("duplicate_pu_code", "missing_pu_code", "missing_geography"):
            assert r.excluded_from == ["all"]
        if s in ("unmatched_admin_label", "invalid_coordinates"):
            assert r.row_status == "eligible_with_warnings" and r.excluded_from == []
    neg = [r for r in result.records if scen[r.source_row_number] == "negative_value"]
    assert all(
        r.row_status == "eligible_with_warnings" and r.excluded_from == [] for r in neg
    )  # rejected votes feed no indicator
    s = result.summary()
    assert s["rows"] == len(rows) and sum(s["row_status"].values()) == len(rows)


def test_without_reference_list_v011_is_not_evaluated(demo):
    columns, rows, _, _ = demo
    result = validate(columns, rows, MAPPING, None)
    assert not any(i.rule_id == "V011" for i in result.issues)
    assert result.summary()["reference_list_used"] is False


def test_incomplete_mapping_produces_only_mapping_issues():
    bad = {**MAPPING, "fields": {k: v for k, v in MAPPING["fields"].items() if k != "ward"}}
    result = validate(list(MAPPING["fields"].values()) + ["Votes PA", "Votes PB", "Votes PC", "Votes PD", "Votes PE"],
                      [("r", 2, [])], bad)  # fmt: skip
    assert result.records == [] and {i.rule_id for i in result.mapping_issues} == {"V001"}


def _row(**over):
    base = {"PU Code": "A", "State": "S", "LGA": "L", "Ward": "W", "Registered Voters": "100",
            "Accredited Voters": "50", "Valid Votes": "48", "Rejected Votes": "2", "Total Votes Cast": "50",
            "Latitude": "", "Longitude": "", "Votes PA": "30", "Votes PB": "18", "Votes PC": "0", "Votes PD": "0",
            "Votes PE": "0"}  # fmt: skip
    base.update(over)
    return base


@pytest.mark.parametrize(
    "over,rules",
    [
        ({}, set()),
        ({"Registered Voters": " 100 "}, set()),
        ({"Registered Voters": "1,000"}, {"V004"}),
        ({"Registered Voters": "1e3"}, {"V004"}),
        ({"Registered Voters": "-1"}, {"V005"}),
        ({"Ward": "  "}, {"V003"}),
        ({"Accredited Voters": "120", "Total Votes Cast": "50"}, {"V006"}),
        ({"Total Votes Cast": "60", "Accredited Voters": "60", "Registered Voters": "100"}, {"V007"}),
        ({"Votes PA": "31"}, {"V008"}),
        ({"Votes PA": "60", "Votes PB": "0"}, {"V008", "V009"}),
        ({"Latitude": "9.1"}, {"V012"}),
        ({"Latitude": "9.1", "Longitude": "7.4"}, set()),
        ({"Valid Votes": "abc"}, {"V004"}),  # V007/V008/V009 not evaluated: one bad cell, one report
    ],
)
def test_single_row_rules(over, rules):
    row = _row(**over)
    cols = list(row)
    result = validate(cols, [("r1", 2, list(row.values()))], MAPPING)
    assert {i.rule_id for i in result.issues} == rules


# Schema 1.1.0: rejected votes and total cast are optional; a partial party list is allowed.
PARTIAL = {
    **MAPPING,
    "schema_version": "1.1.0",
    "fields": {k: v for k, v in MAPPING["fields"].items() if k not in ("rejected_votes", "total_cast")},
    "party_columns": MAPPING["party_columns"][:2],
    "party_columns_complete": False,
}


@pytest.mark.parametrize(
    "over,rules",
    [
        ({}, set()),
        ({"Votes PA": "20", "Votes PB": "10"}, set()),  # other parties may hold the rest
        ({"Votes PA": "40", "Votes PB": "10"}, {"V008"}),  # listed parties exceed valid votes
        ({"Valid Votes": "51", "Votes PB": "21"}, {"V007"}),  # valid votes exceed accredited (50)
        ({"Rejected Votes": "", "Total Votes Cast": "x"}, set()),  # unmapped columns are not read
        ({"Valid Votes": ""}, {"V003"}),
    ],
)
def test_partial_party_list_without_totals(over, rules):
    row = _row(**over)
    result = validate(list(row), [("r1", 2, list(row.values()))], PARTIAL)
    assert {i.rule_id for i in result.issues} == rules


def test_partial_mapping_needs_no_rejected_or_total():
    assert check_mapping(list(_row()), PARTIAL) == []


def test_rejected_without_total_is_compared_with_accreditation():
    m = {**PARTIAL, "fields": {**PARTIAL["fields"], "rejected_votes": "Rejected Votes"}}
    row = _row(**{"Rejected Votes": "3"})  # 48 valid + 3 rejected > 50 accredited
    result = validate(list(row), [("r1", 2, list(row.values()))], m)
    assert [i.rule_id for i in result.issues] == ["V007"]


def test_complete_flag_defaults_to_exact_match():
    m = {k: v for k, v in PARTIAL.items() if k != "party_columns_complete"}
    row = _row(**{"Votes PA": "20"})  # 20 + 18 = 38, valid 48
    assert [i.rule_id for i in validate(list(row), [("r1", 2, list(row.values()))], m).issues] == ["V008"]
    assert validate(list(row), [("r1", 2, list(row.values()))], PARTIAL).issues == []


def test_ragged_row_missing_cells_are_v003():
    row = _row()
    cols = list(row)
    result = validate(cols, [("r1", 2, list(row.values())[:3])], MAPPING)
    assert {i.rule_id for i in result.issues} == {"V003"}
    assert result.records[0].excluded_from == ["all"]


def test_deterministic(demo):
    columns, rows, units, _ = demo
    a, b = validate(columns, rows, MAPPING, units), validate(columns, rows, MAPPING, units)
    assert [vars(i) for i in a.issues] == [vars(i) for i in b.issues]
    assert a.summary() == b.summary()


def test_rule_severity_used_in_issues(demo):
    columns, rows, units, _ = demo
    for i in validate(columns, rows, MAPPING, units).issues:
        assert i.severity == RULES[i.rule_id]["severity"]


def test_counts_by_rule(demo):
    columns, rows, units, truth = demo
    got = Counter(i.rule_id for i in validate(columns, rows, MAPPING, units).issues)
    exp = Counter(r for t in truth.values() for r in filter(None, t["expected_rules"].split("|")))
    assert got == exp


def test_catalogue_matches_published_json_schema():
    import json

    import jsonschema

    schema = json.loads((REPO / "packages/contracts/schemas/validation-rule.schema.json").read_text())
    for d in definitions():
        jsonschema.validate(d, schema)
