"""Collation check (Phase 10): declared ward / LGA / state totals vs the sum of their polling units.

Pure functions, no I/O, no AI. The declared totals come from collation forms (EC8B ward, EC8C LGA, EC8D state)
typed into a CSV. Each declared figure is compared with the sum of the same figure over the polling units of that
area in the uploaded results. A figure is compared only when every polling unit in the area has it; otherwise it
is "not checked" (with the number of units missing it), so an incomplete file never looks like a mismatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.ingest.csv_parse import ParseError, parse_csv

ENGINE_VERSION = "1.0.0"
MAX_DECLARED_ROWS = 5000
MAX_COUNT = 100_000_000
LEVEL_LABEL = {"ward": "ward", "lga": "LGA", "state": "state"}
FIELDS = [
    ("registered_voters", "Registered voters"),
    ("accredited_voters", "Accredited voters"),
    ("valid_votes", "Total valid votes"),
    ("rejected_votes", "Rejected votes"),
    ("total_cast", "Total votes cast"),
]
ALIASES = {
    "level": "level",
    "state": "state",
    "lga": "lga",
    "localgovernmentarea": "lga",
    "lganame": "lga",
    "ward": "ward",
    "registrationarea": "ward",
    "wardname": "ward",
    "registeredvoters": "registered_voters",
    "registered": "registered_voters",
    "votersontheregister": "registered_voters",
    "accreditedvoters": "accredited_voters",
    "accredited": "accredited_voters",
    "validvotes": "valid_votes",
    "totalvalidvotes": "valid_votes",
    "valid": "valid_votes",
    "rejectedvotes": "rejected_votes",
    "rejected": "rejected_votes",
    "rejectedballots": "rejected_votes",
    "totalcast": "total_cast",
    "totalvotescast": "total_cast",
    "votescast": "total_cast",
    "totalusedballotpapers": "total_cast",
    "total": "total_cast",
}
INT_RE = re.compile(r"^\d{1,3}(,\d{3})+$|^\d+$")


class DeclaredError(ValueError):
    """The declared-totals file cannot be used; the message is safe to show."""


def norm_label(s: str | None) -> str:
    return " ".join((s or "").split()).casefold()


def norm_header(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.casefold())


def party_code(header: str) -> str:
    h = re.sub(r"^\s*votes?\s*[:_\- ]?\s*", "", header.strip(), flags=re.I)
    return re.sub(r"[^A-Z0-9]", "", h.upper())


def _int(v: str | None) -> int | None | str:
    """int, None for blank, or the string 'bad' for anything else."""
    s = (v or "").strip()
    if not s:
        return None
    if not INT_RE.match(s):
        return "bad"
    n = int(s.replace(",", ""))
    return n if n <= MAX_COUNT else "bad"


@dataclass
class Declared:
    rows: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    parties: list[str] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)


def parse_declared(data: bytes) -> Declared:
    try:
        parsed = parse_csv(data)
    except ParseError as e:
        raise DeclaredError(e.message) from e
    cols = parsed.columns
    known: dict[int, str] = {}
    for i, c in enumerate(cols):
        f = ALIASES.get(norm_header(c))
        if f and f not in known.values():
            known[i] = f
    if "state" not in known.values():
        raise DeclaredError("The file needs a State column (and LGA and Ward columns for LGA and ward totals).")
    out = Declared()
    party_cols: dict[int, str] = {}
    for i, c in enumerate(cols):
        if i in known:
            continue
        vals = [cells[i] for _, cells in parsed.rows if i < len(cells) and cells[i].strip()]
        code = party_code(c)
        if vals and code and all(_int(v) not in ("bad",) for v in vals) and code not in party_cols.values():
            party_cols[i] = code
        else:
            out.ignored_columns.append(c)
    out.parties = list(party_cols.values())
    if len(parsed.rows) > MAX_DECLARED_ROWS:
        raise DeclaredError(f"The file has more than {MAX_DECLARED_ROWS:,} rows. Split it by state.")
    seen: set[tuple[str, str, str, str]] = set()
    for row_no, cells in parsed.rows:
        get = {f: (cells[i] if i < len(cells) else "") for i, f in known.items()}
        state, lga, ward = (" ".join((get.get(k) or "").split()) for k in ("state", "lga", "ward"))
        level = (get.get("level") or "").strip().lower()
        level = {"local government": "lga", "local government area": "lga"}.get(level, level)
        if level not in LEVEL_LABEL:
            level = "ward" if ward else "lga" if lga else "state"
        missing = [
            n
            for n, v in (("state", state), ("LGA", lga), ("ward", ward))
            if not v
            and (n == "state" or (n == "LGA" and level in ("lga", "ward")) or (n == "ward" and level == "ward"))
        ]
        if missing:
            out.skipped.append(
                {"row": row_no, "reason": f"No {' or '.join(missing)} given for a {LEVEL_LABEL[level]} total."}
            )
            continue
        key = (
            level,
            norm_label(state),
            norm_label(lga) if level != "state" else "",
            norm_label(ward) if level == "ward" else "",
        )
        if key in seen:
            out.skipped.append({"row": row_no, "reason": "The same area appears twice; only its first row is used."})
            continue
        figures: dict[str, int | None] = {}
        bad = []
        for f, _ in FIELDS:
            v = _int(get.get(f)) if f in get else None
            if v == "bad":
                bad.append(f)
                v = None
            figures[f] = v  # type: ignore[assignment]
        parties: dict[str, int] = {}
        for i, code in party_cols.items():
            v = _int(cells[i] if i < len(cells) else "")
            if v == "bad":
                bad.append(code)
            elif v is not None:
                parties[code] = v  # type: ignore[assignment]
        if bad:
            out.skipped.append({"row": row_no, "reason": f"Not whole numbers: {', '.join(bad)}."})
            continue
        if all(v is None for v in figures.values()) and not parties:
            out.skipped.append({"row": row_no, "reason": "No figures on this row."})
            continue
        seen.add(key)
        out.rows.append(
            {
                "row": row_no,
                "level": level,
                "state": state,
                "lga": lga if level != "state" else None,
                "ward": ward if level == "ward" else None,
                "figures": figures,
                "parties": parties,
            }
        )
    if not out.rows:
        raise DeclaredError("No usable rows. Each row needs an area (state, LGA, ward) and at least one figure.")
    return out


@dataclass
class Agg:
    label: tuple[str, str | None, str | None]
    units: int = 0
    duplicates: int = 0
    sums: dict[str, int] = field(default_factory=dict)
    have: dict[str, int] = field(default_factory=dict)  # units that have the figure


def aggregate(records: list[dict[str, Any]]) -> dict[tuple[str, str, str, str], Agg]:
    """Sum polling-unit figures per state, LGA and ward. A polling-unit code that appears twice is counted once."""
    out: dict[tuple[str, str, str, str], Agg] = {}
    seen_codes: set[str] = set()
    for r in records:
        code = re.sub(r"[^0-9A-Z]+", "/", (r.get("pu_code") or "").upper()).strip("/")
        dup = bool(code) and code in seen_codes
        if code:
            seen_codes.add(code)
        s, lg, w = norm_label(r.get("state")), norm_label(r.get("lga")), norm_label(r.get("ward"))
        if not s:
            continue
        for key, label in (
            (("state", s, "", ""), (r["state"], None, None)),
            (("lga", s, lg, ""), (r["state"], r.get("lga"), None)),
            (("ward", s, lg, w), (r["state"], r.get("lga"), r.get("ward"))),
        ):
            if key[0] != "state" and not key[2] or key[0] == "ward" and not key[3]:
                continue
            a = out.setdefault(key, Agg(label=label))  # type: ignore[arg-type]
            if dup:
                a.duplicates += 1
                continue
            a.units += 1
            for f, _ in FIELDS:
                if r.get(f) is not None:
                    a.sums[f] = a.sums.get(f, 0) + int(r[f])
                    a.have[f] = a.have.get(f, 0) + 1
            for p, v in (r.get("party_votes") or {}).items():
                if v is None:
                    continue
                k = f"party:{party_code(p)}"
                a.sums[k] = a.sums.get(k, 0) + int(v)
                a.have[k] = a.have.get(k, 0) + 1
    return out


def _figure(fld: str, label: str, declared: int, a: Agg) -> dict[str, Any]:
    have = a.have.get(fld, 0)
    missing = a.units - have
    data = a.sums.get(fld, 0) if have else None
    complete = missing == 0 and a.units > 0
    return {
        "field": fld,
        "label": label,
        "declared": declared,
        "data": data,
        "diff": (declared - data) if complete and data is not None else None,
        "match": (declared == data) if complete else None,
        "missing_units": missing,
    }


def compare(declared: Declared, aggs: dict[tuple[str, str, str, str], Agg]) -> list[dict[str, Any]]:
    areas = []
    for d in declared.rows:
        key = (d["level"], norm_label(d["state"]), norm_label(d["lga"]), norm_label(d["ward"]))
        a = aggs.get(key)
        area = {
            "level": d["level"],
            "state": d["state"],
            "lga": d["lga"],
            "ward": d["ward"],
            "row": d["row"],
            "units": a.units if a else 0,
            "duplicate_units": a.duplicates if a else 0,
            "figures": [],
        }
        if a is None or a.units == 0:
            area["status"] = "unmatched"
            areas.append(area)
            continue
        figs = [_figure(f, label, d["figures"][f], a) for f, label in FIELDS if d["figures"].get(f) is not None]
        figs += [_figure(f"party:{p}", f"{p} votes", v, a) for p, v in d["parties"].items()]
        area["figures"] = figs
        area["status"] = (
            "mismatch"
            if any(x["match"] is False for x in figs)
            else "incomplete"
            if any(x["match"] is None for x in figs)
            else "match"
        )
        areas.append(area)
    return areas


def area_name(a: dict[str, Any]) -> str:
    return " / ".join(x for x in (a["state"], a.get("lga"), a.get("ward")) if x)


def severity(a: dict[str, Any]) -> str:
    """high when a vote figure is off by at least 100 votes or 1% of the declared figure; otherwise medium."""
    for x in a["figures"]:
        if x["match"] is False and (x["field"].startswith("party:") or x["field"] in ("valid_votes", "total_cast")):
            if abs(x["diff"]) >= max(100, 0.01 * x["declared"]):
                return "high"
    return "medium"


def describe(a: dict[str, Any]) -> str:
    diffs = [x for x in a["figures"] if x["match"] is False]
    parts = "; ".join(
        f"{x['label']}: declared {x['declared']:,}, polling units add up to {x['data']:,} ({x['diff']:+,})"
        for x in diffs[:8]
    )
    extra = f" and {len(diffs) - 8} more" if len(diffs) > 8 else ""
    dup = f" {a['duplicate_units']} repeated polling-unit row(s) were counted once." if a["duplicate_units"] else ""
    return (
        f"{len(diffs)} figure(s) differ across {a['units']} polling unit(s) in the uploaded results: "
        f"{parts}{extra}.{dup}"
    )


LIMITATIONS = (
    "A declared total and the sum of polling-unit results can differ for recorded reasons: results cancelled or "
    "excluded at collation, polling units missing from the uploaded file, corrections after the count, or errors "
    "in either source. Compare with the collation form and the polling-unit result sheets before drawing any "
    "conclusion."
)
