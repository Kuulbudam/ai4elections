"""Track 2 evidence: the rules find every planted problem in the organisers' starter data, with no false alarms."""

from pathlib import Path

from app.evaluation import run

DATA = Path(__file__).resolve().parents[3] / "data/rise-starter"


def test_rules_find_every_planted_problem_without_false_alarms():
    r = run(DATA)
    assert (r["records"], r["planted"]) == (900, 42)
    assert r["rules_only"] == {"flagged": 42, "true_positives": 42, "false_alarms": 0, "missed": 0, "precision": 1.0,
                               "recall": 1.0, "alerts_per_1000": 46.7}  # fmt: skip
    assert {t: e["found"] == e["planted"] for t, e in r["by_type"].items()} == dict.fromkeys(r["by_type"], True)
    # The statistical layer adds outliers the answer key does not plant; they are reported, not hidden.
    assert r["rules_and_statistics"]["recall"] == 1.0 and r["statistical_only_flags"] == 44
