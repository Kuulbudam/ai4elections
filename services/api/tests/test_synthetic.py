"""The synthetic generator is deterministic, the committed demo file matches it, and its ground
truth is honest: rows labelled normal break no rule, and every planted row breaks what it says."""

import csv
import importlib.util
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("synthetic_generate", REPO / "tools/synthetic/generate.py")
gen = importlib.util.module_from_spec(spec)
sys.modules["synthetic_generate"] = gen
spec.loader.exec_module(gen)


@pytest.fixture(scope="module")
def built():
    return gen.build(2026)


def test_committed_demo_files_match_generator(tmp_path, built):
    rows, units = built
    paths = gen.write(rows, units, 2026, tmp_path)
    for key, p in paths.items():
        committed = REPO / "data/synthetic" / p.name
        assert committed.read_bytes() == p.read_bytes(), f"{key} out of date: run tools/synthetic/generate.py"


def test_size_and_shape(built):
    rows, _ = built
    assert 11_000 <= len(rows) <= 13_000
    assert (REPO / "data/synthetic/SYNTHETIC_results_seed2026.csv").stat().st_size < 25 * 1024 * 1024


def test_normal_rows_break_no_rule(built):
    rows, units = built
    unit_set = set(units)
    codes = Counter(r.pu_code for r in rows)
    for r in rows:
        if r.scenario not in ("normal", "small_ward_lga_fallback", "small_peer_group", "zero_mad_peer_group"):
            continue
        assert not r.raw and not r.lat and not r.lon
        assert r.pu_code and codes[r.pu_code] == 1
        assert (r.state, r.lga, r.ward) in unit_set
        assert 0 <= r.total_cast <= r.accredited <= r.registered
        assert r.total_cast == r.valid + r.rejected
        assert sum(r.votes) == r.valid and all(v >= 0 for v in r.votes)
        # The zero-MAD ward is 12 identical units with round scores: the pattern rules (1.2.0) find it.
        allowed = {"V013", "V014"} if r.scenario == "zero_mad_peer_group" else set()
        assert r.expected_rules <= allowed and not r.expected_flags


def test_every_planted_scenario_present(built):
    rows, _ = built
    scen = Counter(r.scenario for r in rows)
    for s in [
        "duplicate_pu_code", "invalid_numeric", "missing_pu_code", "accredited_exceeds_registered",
        "reconciliation_error", "abnormal_turnout", "unusual_leading_share", "missing_geography",
        "unmatched_admin_label", "small_peer_group", "zero_mad_peer_group", "invalid_coordinates",
        "party_sum_mismatch", "negative_value", "count_exceeds_denominator",
    ]:  # fmt: skip
        assert scen[s] >= 5, s


def test_planted_rows_really_break_their_rule(built):
    rows, units = built
    unit_set = set(units)
    for r in rows:
        if "V006" in r.expected_rules:
            assert r.accredited > r.registered
        if "V007" in r.expected_rules:
            assert r.total_cast != r.valid + r.rejected or r.total_cast > r.accredited
        if "V008" in r.expected_rules:
            assert sum(r.votes) != r.valid
        if "V011" in r.expected_rules:
            assert (r.state, r.lga, r.ward) not in unit_set
        if "turnout_rate" in r.expected_flags:
            assert r.accredited / r.registered > 0.85
        if "leading_party_share" in r.expected_flags:
            assert max(r.votes) / r.valid > 0.9


# The hackathon organisers' starter files sit in the same folder in the submission repository (fork of their
# template); they are fictional too and named by them.
ORGANISERS = {"results_synthetic.csv", "polling_units_synthetic.csv", "anomaly_labels_HELD_OUT.csv",
              "incident_reports_synthetic.csv", "logistics_deliveries_synthetic.csv", "voter_info_faq_multilingual.csv",
              "accessibility_barriers_survey_synthetic.csv"}  # fmt: skip


def test_files_are_labelled_synthetic():
    for p in (REPO / "data/synthetic").glob("*.csv"):
        assert p.name.startswith("SYNTHETIC_") or p.name in ORGANISERS
    assert "fictional" in (REPO / "data/synthetic/README.md").read_text().lower()


def test_ground_truth_rows_line_up_with_results():
    with (REPO / "data/synthetic/SYNTHETIC_results_seed2026.csv").open() as f:
        results = list(csv.reader(f))
    with (REPO / "data/synthetic/SYNTHETIC_ground_truth_seed2026.csv").open() as f:
        truth = list(csv.DictReader(f))
    assert len(truth) == len(results) - 1
    for t in truth[:50]:
        assert results[int(t["source_row_number"]) - 1][0] == t["pu_code"]
