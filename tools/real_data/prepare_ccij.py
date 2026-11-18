"""Build an upload-ready CSV of real 2023 presidential polling-unit results for one state.

Source: the Centre for Collaborative Investigative Journalism (CCIJ) and VEZA analysis of the
2023 Nigerian presidential election, https://github.com/anndawn/Nigeria-2023-election
(data read from INEC's IReV result sheets with OCR, plus registered and BVAS-accredited voter
counts). The source data is NOT stored in this repository: this script downloads it, pinned to a
fixed commit, each time it runs. See docs/adr/0007-real-data-ccij.md for the licence position and
for what each column means.

Usage:
    python tools/real_data/prepare_ccij.py                      # Plateau, download from GitHub
    python tools/real_data/prepare_ccij.py --state KANO
    python tools/real_data/prepare_ccij.py --source-dir /path/to/clone   # use a local clone

Writes (in data/real/, which git ignores):
    REAL_CCIJ_2023_presidential_<state>.csv   one row per polling unit, ready to upload
    REAL_CCIJ_2023_reference_<state>.csv      state, lga, ward (for the project's reference list)
    REAL_CCIJ_2023_<state>_SOURCE.txt         attribution, commit, file hashes, row counts

Nothing is corrected or filled in. A blank cell in the source stays blank, so a result sheet that
could not be read shows up in validation as a missing value (V003), not as zero.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import sys
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

REPO_URL = "https://github.com/anndawn/Nigeria-2023-election"
COMMIT = "e38e3bc9a22ac005564a9e95b4e846208a594522"
RAW = f"https://raw.githubusercontent.com/anndawn/Nigeria-2023-election/{COMMIT}"
FILES = ["voter_info.csv", "AllPollingUnitsInfo.csv"]
PARTIES = ["APC", "PDP", "LP", "NNPP"]  # the only parties the source extracted
OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "real"

HEADER = [
    "PU Code", "State", "LGA", "Ward", "Polling Unit",
    "Registered Voters", "Accredited Voters", "Valid Votes",
    *[f"Votes {p}" for p in PARTIES],
    "Latitude", "Longitude", "Source Status", "Source Document",
]  # fmt: skip

ATTRIBUTION = (
    "Source: Centre for Collaborative Investigative Journalism (CCIJ) and VEZA, analysis of the 2023 "
    f"Nigerian presidential election polling-unit results ({REPO_URL}). Figures were read from INEC "
    "IReV result sheets by OCR and may contain reading errors. Prepared for AI4Elections by "
    "tools/real_data/prepare_ccij.py; values are unchanged apart from removing a trailing '.0'."
)


def fetch(name: str, source_dir: Path | None) -> bytes:
    if source_dir is not None:
        return (source_dir / name).read_bytes()
    with urllib.request.urlopen(f"{RAW}/{name}", timeout=120) as r:  # noqa: S310 (fixed https URL)
        return r.read()


def count(v: str | None) -> str:
    """'266.0' -> '266'. Anything that is not a whole number is passed through unchanged."""
    s = (v or "").strip()
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def build(voter_info: bytes, pu_info: bytes, state: str) -> tuple[list[list[str]], list[list[str]], Counter]:
    info = {r["polling_unit_code"]: r for r in csv.DictReader(io.StringIO(pu_info.decode("utf-8-sig")))}
    rows: list[list[str]] = []
    units: set[tuple[str, str, str]] = set()
    stats: Counter = Counter()
    for r in csv.DictReader(io.StringIO(voter_info.decode("utf-8-sig"))):
        i = info.get(r["polling_unit_code"])
        if i is None or i["state_name"].strip().upper() != state:
            continue
        stats["rows"] += 1
        stats[f"status: {r['status']}"] += 1
        if count(r["total_use"]) == "":
            stats["valid votes blank"] += 1
        st, lga, ward = (
            i["state_name"].strip(),
            i["lga_name"].strip(),
            i["ward_name"].strip(),
        )
        units.add((st, lga, ward))
        rows.append([
            r["polling_unit_code"].strip(), st, lga, ward, i["unit_name"].strip(),
            count(r["registered_use"]), count(r["Accredited_num"]), count(r["total_use"]),
            *[count(r[p]) for p in PARTIES],
            i["lat"].strip(), i["lng"].strip(), r["status"].strip(), i["URL"].strip(),
        ])  # fmt: skip
    rows.sort(key=lambda x: x[0])
    ref = [list(u) for u in sorted(units)]
    return rows, ref, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", default="PLATEAU")
    ap.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="local clone instead of downloading",
    )
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)
    state = a.state.strip().upper()

    raw = {n: fetch(n, a.source_dir) for n in FILES}
    rows, ref, stats = build(raw["voter_info.csv"], raw["AllPollingUnitsInfo.csv"], state)
    if not rows:
        print(f"No polling units found for state {state!r}.", file=sys.stderr)
        return 1

    a.out_dir.mkdir(parents=True, exist_ok=True)
    slug = state.lower().replace(" ", "_")
    data_path = a.out_dir / f"REAL_CCIJ_2023_presidential_{slug}.csv"
    ref_path = a.out_dir / f"REAL_CCIJ_2023_reference_{slug}.csv"
    with data_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    with ref_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["state", "lga", "ward"])
        w.writerows(ref)

    lines = [
        ATTRIBUTION,
        "",
        f"Prepared: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"Source commit: {COMMIT}",
        *[f"sha256 {n}: {hashlib.sha256(b).hexdigest()}" for n, b in raw.items()],
        f"State: {state}",
        f"Polling units: {len(rows)}; administrative units (state/LGA/ward): {len(ref)}",
        *[f"{k}: {v}" for k, v in sorted(stats.items()) if k != "rows"],
        "",
        "Column notes:",
        "  Registered Voters  - INEC register count per polling unit (source column registered_use)",
        "  Accredited Voters  - BVAS accreditation synced to IReV (source column Accredited_num)",
        "  Valid Votes        - the source's best reading of the sheet's total (source column total_use)",
        "  Votes APC/PDP/LP/NNPP - only these four parties were extracted; map them as a partial list",
        "  No rejected-votes or total-cast column exists in the source; leave both unmapped.",
        "  Source Status      - the source's assessment of the result sheet image (e.g. blurred)",
        "  Source Document    - link to the result sheet image on DocumentCloud",
    ]
    (a.out_dir / f"REAL_CCIJ_2023_{slug}_SOURCE.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWrote {data_path}\nWrote {ref_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
