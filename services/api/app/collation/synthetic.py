"""SYNTHETIC declared totals for tests and the demo: ward (EC8B-style), LGA (EC8C-style) and state rows built
from the synthetic results file, with three planted differences. Not real collation results.

`python -m app.collation.synthetic --out file.csv` writes the demo file.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
from collections import defaultdict
from pathlib import Path

from app.sheets.synthetic import SYNTHETIC_CSV

FIELDS = ["Registered Voters", "Accredited Voters", "Valid Votes", "Rejected Votes", "Total Votes Cast"]
PARTIES = ["PA", "PB", "PC", "PD", "PE"]
LGA = ("Synthia", "Amber North")
# Planted differences: (level, ward or None, column, change)
PLANTED = [
    ("ward", "Amber Ward 2", "PA", 120),
    ("ward", "Amber Ward 3", "Valid Votes", -40),
    ("lga", None, "Total Votes Cast", 35),
]


def _int(s: str) -> int | None:
    s = (s or "").strip()
    return int(s) if re.fullmatch(r"\d+", s) else None


def build(path: Path = SYNTHETIC_CSV) -> str:
    wards: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    lga: dict[str, int] = defaultdict(int)
    state: dict[str, int] = defaultdict(int)
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = (r["PU Code"] or "").strip().upper()
            if code and code in seen:
                continue  # repeated rows are counted once, as the platform does
            seen.add(code)
            if r["State"].strip() != LGA[0]:
                continue
            targets = [state]
            if r["LGA"].strip() == LGA[1]:
                targets += [lga, wards[" ".join(r["Ward"].split())]]
            for col in [*FIELDS, *(f"Votes {p}" for p in PARTIES)]:
                v = _int(r[col])
                if v is not None:
                    for t in targets:
                        t[col.removeprefix("Votes ")] += v
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["Level", "State", "LGA", "Ward", *FIELDS, *PARTIES, "Source"])

    def row(level: str, ward: str | None, sums: dict[str, int]) -> None:
        vals = dict(sums)
        for lv, wd, col, change in PLANTED:
            if lv == level and wd == ward:
                vals[col] = vals.get(col, 0) + change
        w.writerow([level, LGA[0], LGA[1] if level != "state" else "", ward or "",
                    *(vals.get(c, 0) for c in FIELDS), *(vals.get(p, 0) for p in PARTIES),
                    {"ward": "SYNTHETIC EC8B", "lga": "SYNTHETIC EC8C", "state": "SYNTHETIC EC8D"}[level]])  # fmt: skip

    for name in sorted(wards):
        row("ward", name, wards[name])
    row("lga", None, lga)
    row("state", None, state)
    return out.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    Path(ap.parse_args().out).write_text(build(), encoding="utf-8")


if __name__ == "__main__":
    main()
