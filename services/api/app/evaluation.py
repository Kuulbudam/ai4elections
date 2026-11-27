"""Score the checks against the organisers' starter data (Rise Networks #AI4Elections, Track 2).

The starter pack has 900 fictional polling units with about 5% planted problems and an answer key. Each row is run
through the same validation rules and analysis the platform uses. Precision, recall and alerts per 1,000 records are
reported for the rules alone and for rules plus the statistical outlier layer. Pure: reads three CSV files.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.analysis.engine import Unit, analyse
from app.validation.engine import validate

PARTIES = ["votes_A", "votes_B", "votes_C", "votes_D", "votes_E"]
FIELDS = ["pu_code", "state", "lga", "ward", "registered_voters", "accredited_voters", "valid_votes", "rejected_votes"]


def run(folder: Path) -> dict[str, Any]:
    res = list(csv.DictReader((folder / "results_synthetic.csv").open()))
    units = {r["pu_id"]: r for r in csv.DictReader((folder / "polling_units_synthetic.csv").open())}
    truth = {r["pu_id"]: r["anomaly_type"] for r in csv.DictReader((folder / "anomaly_labels_HELD_OUT.csv").open())}
    columns = [*FIELDS, *PARTIES]
    rows = []
    for i, r in enumerate(res):
        u = units[r["pu_id"]]
        valid = sum(int(r[p]) for p in PARTIES)  # the file has no separate valid-votes column
        cells = [r["pu_id"], u["state"], u["lga"], u["ward"], r["registered_voters"], r["accredited_voters"],
                 str(valid), r["rejected_votes"], *[r[p] for p in PARTIES]]  # fmt: skip
        rows.append((f"r{i}", i + 2, cells))
    mapping = {
        "fields": {f: f for f in FIELDS},
        "party_columns": [{"party_code": p[-1], "source_column": p} for p in PARTIES],
    }
    v = validate(columns, rows, mapping)
    a = analyse(
        [Unit(r.raw_row_id, r.source_row_number, r.values["pu_code"], r.values["state"], r.values["lga"],
              r.values["ward"], r.values["registered_voters"], r.values["accredited_voters"], r.values["valid_votes"],
              r.party_votes, r.row_status, r.excluded_from, r.exclusion_reasons) for r in v.records],
        [i.__dict__ for i in v.issues],
    )  # fmt: skip
    code = {r.raw_row_id: r.values["pu_code"] for r in v.records}
    rules: dict[str, set[str]] = defaultdict(set)
    stats: set[str] = set()
    for f in a.findings:
        if f["category"] == "data_quality_issue":
            rules[code[f["raw_row_id"]]].add(f["rule_id"])
        else:
            stats.add(code[f["raw_row_id"]])
    planted = {k for k, t in truth.items() if t != "none"}

    def score(flagged: set[str]) -> dict[str, Any]:
        tp = len(flagged & planted)
        return {"flagged": len(flagged), "true_positives": tp, "false_alarms": len(flagged - planted),
                "missed": len(planted - flagged), "precision": round(tp / len(flagged), 3) if flagged else None,
                "recall": round(tp / len(planted), 3),
                "alerts_per_1000": round(1000 * len(flagged) / len(truth), 1)}  # fmt: skip

    by_type: dict[str, dict[str, Any]] = {}
    for k, t in truth.items():
        if t == "none":
            continue
        e = by_type.setdefault(t, {"planted": 0, "found": 0, "rules": set()})
        e["planted"] += 1
        e["found"] += k in rules
        e["rules"] |= rules.get(k, set())
    return {
        "records": len(truth),
        "planted": len(planted),
        "rules_only": score(set(rules)),
        "rules_and_statistics": score(set(rules) | stats),
        "statistical_only_flags": len(stats - set(rules)),
        "by_type": {t: {**e, "rules": sorted(e["rules"])} for t, e in sorted(by_type.items())},
        "issues_by_rule": v.summary()["issues_by_rule"],
    }
