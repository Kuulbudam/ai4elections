"""Synthetic election results generator (master prompt Section 10).

Everything produced here is FICTIONAL: invented states, LGAs, wards, polling units, parties and
numbers. No coordinates are given for normal rows, so nothing can be mistaken for a real place.

    python tools/synthetic/generate.py --seed 2026 --out data/synthetic

Writes (deterministic for a given seed):
    SYNTHETIC_results_seed<seed>.csv        the upload file (~12,000 polling units)
    SYNTHETIC_ground_truth_seed<seed>.csv   per-row expected validation rules / statistical flags
    SYNTHETIC_admin_units_seed<seed>.csv    reference list of valid state/LGA/ward labels (for V011)
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass, field
from pathlib import Path

PARTIES = ["PA", "PB", "PC", "PD", "PE"]
HEADER = [
    "PU Code", "State", "LGA", "Ward", "Registered Voters", "Accredited Voters", "Valid Votes",
    "Rejected Votes", "Total Votes Cast", *[f"Votes {p}" for p in PARTIES], "Latitude", "Longitude",
]  # fmt: skip
STATES = ["Synthia", "Norvale", "Eastmarch", "Quillon"]
LGA_STEMS = ["Amber", "Brook", "Cinder", "Dale", "Ember", "Fallow", "Glen", "Harrow", "Iris", "Juniper"]
SUFFIXES = ["North", "South", "Central", "East", "West", "Hills"]


@dataclass
class Row:
    pu_code: str
    state: str
    lga: str
    ward: str
    registered: int
    accredited: int
    valid: int
    rejected: int
    total_cast: int
    votes: list[int]
    lat: str = ""
    lon: str = ""
    raw: dict[str, str] = field(default_factory=dict)  # overrides for planted bad cells
    scenario: str = "normal"
    expected_rules: set[str] = field(default_factory=set)
    expected_flags: set[str] = field(default_factory=set)
    expected_not_scored: str = ""

    def cells(self) -> list[str]:
        base = {
            "PU Code": self.pu_code,
            "State": self.state,
            "LGA": self.lga,
            "Ward": self.ward,
            "Registered Voters": str(self.registered),
            "Accredited Voters": str(self.accredited),
            "Valid Votes": str(self.valid),
            "Rejected Votes": str(self.rejected),
            "Total Votes Cast": str(self.total_cast),
            **{f"Votes {p}": str(v) for p, v in zip(PARTIES, self.votes, strict=True)},
            "Latitude": self.lat,
            "Longitude": self.lon,
        }
        base.update(self.raw)
        return [base[h] for h in HEADER]


def split_votes(rng: random.Random, total: int, weights: list[float]) -> list[int]:
    """Multinomial-style split of `total` that always sums exactly to `total`."""
    s = sum(weights)
    shares = [w / s for w in weights]
    out = [int(total * sh) for sh in shares]
    remainder = total - sum(out)
    for _ in range(remainder):
        out[rng.choices(range(len(out)), weights=shares)[0]] += 1
    return out


def make_row(rng, code, state, lga, ward, turnout, prefs) -> Row:
    registered = rng.randint(300, 1500)
    t = min(0.95, max(0.05, rng.gauss(turnout, 0.03)))
    accredited = round(registered * t)
    total_cast = accredited - rng.randint(0, max(1, accredited // 60))
    rejected = round(total_cast * rng.uniform(0.01, 0.03))
    valid = total_cast - rejected
    weights = [max(0.01, p + rng.gauss(0, 0.03)) for p in prefs]
    return Row(
        code, state, lga, ward, registered, accredited, valid, rejected, total_cast, split_votes(rng, valid, weights)
    )


def build(seed: int) -> tuple[list[Row], list[tuple[str, str, str]]]:
    rng = random.Random(seed)  # noqa: S311 - deterministic synthetic data, not security
    rows: list[Row] = []
    units: list[tuple[str, str, str]] = []
    pu_counter = 0

    def code(si: int, li: int, wi: int) -> str:
        nonlocal pu_counter
        pu_counter += 1
        return f"SY-{si + 1:02d}-{li + 1:02d}-{wi + 1:02d}-{pu_counter:05d}"

    for si, state in enumerate(STATES):
        for li in range(6):
            lga = f"{LGA_STEMS[(si * 3 + li) % len(LGA_STEMS)]} {SUFFIXES[li]}"
            lga_turnout = rng.uniform(0.28, 0.45)
            for wi in range(10):
                ward = f"{lga.split()[0]} Ward {wi + 1}"
                units.append((state, lga, ward))
                wturn = min(0.8, max(0.15, rng.gauss(lga_turnout, 0.04)))
                prefs = [rng.uniform(0.5, 1.5) for _ in PARTIES]
                prefs[rng.randrange(len(PARTIES))] *= 2.2  # a locally strong party
                n = rng.randint(38, 62)
                if si == 0 and li == 0 and wi == 9:
                    n = 6  # scenario 10a: small ward -> LGA fallback
                for _ in range(n):
                    r = make_row(rng, code(si, li, wi), state, lga, ward, wturn, prefs)
                    if n < 10:
                        r.scenario = "small_ward_lga_fallback"  # scored against the LGA instead
                    rows.append(r)

    # Scenario 10b: a tiny LGA (8 PUs over 2 wards) -> not scorable at ward or LGA level
    state, lga = "Quillon", "Kestrel Isle"
    for wi in range(2):
        ward = f"Kestrel Ward {wi + 1}"
        units.append((state, lga, ward))
        for _ in range(4):
            r = make_row(rng, code(3, 6, wi), state, lga, ward, 0.4, [1, 1, 1, 1, 2])
            r.scenario = "small_peer_group"
            r.expected_not_scored = "insufficient_peers"
            rows.append(r)

    # Scenario 11: zero-MAD ward (12 identical PUs) -> not scorable (zero MAD)
    state, lga, ward = "Norvale", "Brook South", "Brook Ward 11"
    units.append((state, lga, ward))
    for _ in range(12):
        r = Row(code(1, 1, 10), state, lga, ward, 600, 300, 290, 6, 296, [120, 70, 50, 30, 20])
        r.scenario = "zero_mad_peer_group"
        r.expected_not_scored = "zero_mad"
        rows.append(r)

    plant(rng, rows)
    return rows, units


def plant(rng: random.Random, rows: list[Row]) -> None:
    """Plant documented scenarios on disjoint, randomly chosen normal rows in large wards."""
    ward_sizes: dict[tuple[str, str, str], int] = {}
    for r in rows:
        ward_sizes[(r.state, r.lga, r.ward)] = ward_sizes.get((r.state, r.lga, r.ward), 0) + 1
    pool = [i for i, r in enumerate(rows) if r.scenario == "normal" and ward_sizes[(r.state, r.lga, r.ward)] >= 30]
    rng.shuffle(pool)

    def take(n: int) -> list[Row]:
        return [rows[pool.pop()] for _ in range(n)]

    # 1. duplicate PU identifiers: the second row reuses another row's code
    for a, b in zip(take(5), take(5), strict=True):
        b.pu_code = a.pu_code
        for r in (a, b):
            r.scenario = "duplicate_pu_code"
            r.expected_rules.add("V010")
            r.expected_not_scored = "quarantined"

    # 2. invalid numeric fields
    for r, (col, bad) in zip(
        take(10),
        [("Registered Voters", "abc"), ("Valid Votes", "12.5"), ("Accredited Voters", "1,2O0"),
         ("Rejected Votes", "N/A"), ("Votes PB", "ten"), ("Total Votes Cast", "4e2"),
         ("Registered Voters", "--"), ("Votes PA", "7.0"), ("Accredited Voters", " "), ("Valid Votes", "#REF!")],
        strict=True,
    ):  # fmt: skip
        r.raw[col] = bad
        r.scenario = "invalid_numeric"
        r.expected_rules.add("V003" if not bad.strip() else "V004")

    # 3. missing PU identifier
    for r in take(5):
        r.pu_code = ""
        r.scenario = "missing_pu_code"
        r.expected_rules.add("V003")

    # 4. accredited exceeds registered
    for r in take(8):
        r.accredited = r.registered + rng.randint(5, 80)
        r.total_cast = r.accredited  # keep reconciliation otherwise consistent
        r.rejected = round(r.total_cast * 0.02)
        r.valid = r.total_cast - r.rejected
        r.votes = split_votes(rng, r.valid, [1, 1, 1, 1, 1])
        r.scenario = "accredited_exceeds_registered"
        r.expected_rules.add("V006")

    # 5. vote reconciliation errors: total cast > valid + rejected (and possibly > accredited).
    #    Only upward changes, so no other rule (e.g. V009) is triggered as a side effect.
    for r in take(8):
        r.total_cast = r.valid + r.rejected + rng.randint(7, 31)
        r.scenario = "reconciliation_error"
        r.expected_rules.add("V007")

    # 5b. party votes do not sum to valid votes
    for r in take(6):
        r.votes[0] += rng.choice([15, 40, -12])
        r.votes[0] = max(0, r.votes[0])
        r.scenario = "party_sum_mismatch"
        r.expected_rules.add("V008")

    # 5c. negative values
    for r in take(5):
        r.raw["Rejected Votes"] = str(-rng.randint(1, 20))
        r.scenario = "negative_value"
        r.expected_rules.add("V005")

    # 5d. count exceeds its denominator: a party has more votes than valid votes
    for r in take(5):
        r.votes = [r.valid + rng.randint(5, 50), 0, 0, 0, 0]
        r.scenario = "count_exceeds_denominator"
        r.expected_rules.update({"V009", "V008"})

    # 6. abnormal turnout (statistical)
    for r in take(15):
        t = rng.uniform(0.86, 0.97)
        r.accredited = round(r.registered * t)
        r.total_cast = r.accredited
        r.rejected = round(r.total_cast * 0.015)
        r.valid = r.total_cast - r.rejected
        shares = [v / max(1, sum(r.votes)) for v in r.votes]
        r.votes = split_votes(rng, r.valid, [max(s, 0.01) for s in shares])
        r.scenario = "abnormal_turnout"
        r.expected_flags.add("turnout_rate")

    # 7. unusual leading-party vote share (statistical)
    for r in take(15):
        lead = rng.uniform(0.93, 0.99)
        top = round(r.valid * lead)
        rest = split_votes(rng, r.valid - top, [1, 1, 1, 1])
        winner = rng.randrange(len(PARTIES))
        r.votes = rest[:winner] + [top] + rest[winner:]
        r.scenario = "unusual_leading_share"
        r.expected_flags.add("leading_party_share")

    # 8. missing geography
    for r, col in zip(take(6), ["Ward", "LGA", "Ward", "State", "Ward", "LGA"], strict=True):
        r.raw[col] = ""
        r.scenario = "missing_geography"
        r.expected_rules.add("V003")

    # 9. unmatched administrative labels (typos)
    for r in take(6):
        r.ward = r.ward.replace("Ward", "Wrd") if rng.random() < 0.5 else r.ward + "x"
        r.scenario = "unmatched_admin_label"
        r.expected_rules.add("V011")

    # 12b. invalid coordinates (all other rows have none)
    for r, (lat, lon) in zip(
        take(5), [("91.2", "5.0"), ("4.5", ""), ("", "7.1"), ("abc", "3.2"), ("-95", "200")], strict=True
    ):
        r.lat, r.lon = lat, lon
        r.scenario = "invalid_coordinates"
        r.expected_rules.add("V012")


def write(rows: list[Row], units, seed: int, out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "results": out / f"SYNTHETIC_results_seed{seed}.csv",
        "truth": out / f"SYNTHETIC_ground_truth_seed{seed}.csv",
        "units": out / f"SYNTHETIC_admin_units_seed{seed}.csv",
    }
    with paths["results"].open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEADER)
        for r in rows:
            w.writerow(r.cells())
    with paths["truth"].open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(
            ["source_row_number", "pu_code", "scenario", "expected_rules", "expected_flags", "expected_not_scored"]
        )
        for i, r in enumerate(rows, start=2):  # row 1 is the header
            w.writerow(
                [
                    i,
                    r.pu_code,
                    r.scenario,
                    "|".join(sorted(r.expected_rules)),
                    "|".join(sorted(r.expected_flags)),
                    r.expected_not_scored,
                ]
            )
    with paths["units"].open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["state", "lga", "ward"])
        w.writerows(sorted(set(units)))
    return paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", type=Path, default=Path("data/synthetic"))
    args = ap.parse_args()
    rows, units = build(args.seed)
    paths = write(rows, units, args.seed, args.out)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.scenario] = counts.get(r.scenario, 0) + 1
    print(f"{len(rows)} rows; {paths['results'].stat().st_size / 1e6:.2f} MB")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
