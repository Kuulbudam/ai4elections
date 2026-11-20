"""Checks on a confirmed result sheet. Pure functions, no AI: the person has already confirmed the figures.

1. The sheet's own arithmetic (the rules printed on Form EC8A).
2. Each figure against the matching polling unit in the uploaded results.

Every failed check is a reason to look again at the sheet and the data, never a conclusion about what happened.
"""

from __future__ import annotations

import re
from typing import Any

FIELDS = [
    ("registered_voters", "Registered voters"),
    ("accredited_voters", "Accredited voters"),
    ("valid_votes", "Total valid votes"),
    ("rejected_votes", "Rejected votes"),
    ("total_cast", "Total votes cast"),
]


def norm_code(code: str | None) -> str:
    """Polling-unit codes are written many ways (31/04/01/003, 31-04-01-003, 31 04 01 003)."""
    return re.sub(r"[^0-9A-Z]+", "/", (code or "").strip().upper()).strip("/")


def norm_party(p: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", p.upper())


def _check(cid: str, ok: bool | None, title: str, detail: str, severity: str | None) -> dict[str, Any]:
    return {"id": cid, "ok": ok, "title": title, "detail": detail, "severity": severity if ok is False else None}


def arithmetic(fig: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    parties = fig.get("party_votes") or []
    reg, acc = fig.get("registered_voters"), fig.get("accredited_voters")
    valid, rej, total = fig.get("valid_votes"), fig.get("rejected_votes"), fig.get("total_cast")

    if parties and valid is not None:
        s = sum(p["votes"] for p in parties)
        out.append(
            _check(
                "sheet_party_sum",
                s == valid,
                "Party scores add up to total valid votes",
                f"Party scores add up to {s:,}; the sheet's total valid votes is {valid:,}.",
                "high",
            )
        )
    else:
        out.append(
            _check(
                "sheet_party_sum",
                None,
                "Party scores add up to total valid votes",
                "Not checked: party scores or total valid votes are missing.",
                None,
            )
        )

    if valid is not None and rej is not None and total is not None:
        out.append(
            _check(
                "sheet_total",
                valid + rej == total,
                "Valid plus rejected equals total votes cast",
                f"{valid:,} valid + {rej:,} rejected = {valid + rej:,}; the sheet says {total:,}.",
                "high",
            )
        )
    else:
        out.append(
            _check(
                "sheet_total",
                None,
                "Valid plus rejected equals total votes cast",
                "Not checked: one of valid, rejected or total votes is missing.",
                None,
            )
        )

    used = total if total is not None else (valid + rej if valid is not None and rej is not None else None)
    if used is not None and acc is not None:
        out.append(
            _check(
                "sheet_over_accredited",
                used <= acc,
                "Votes cast do not exceed accredited voters",
                f"{used:,} votes cast against {acc:,} accredited voters.",
                "very_high",
            )
        )
    else:
        out.append(
            _check(
                "sheet_over_accredited",
                None,
                "Votes cast do not exceed accredited voters",
                "Not checked: votes cast or accredited voters missing.",
                None,
            )
        )

    if acc is not None and reg is not None:
        out.append(
            _check(
                "sheet_over_registered",
                acc <= reg,
                "Accredited voters do not exceed registered voters",
                f"{acc:,} accredited against {reg:,} registered.",
                "very_high",
            )
        )
    else:
        out.append(
            _check(
                "sheet_over_registered",
                None,
                "Accredited voters do not exceed registered voters",
                "Not checked: accredited or registered voters missing.",
                None,
            )
        )
    return out


def compare(fig: dict[str, Any], row: dict[str, Any] | None) -> dict[str, Any]:
    """Sheet figures vs the dataset's polling-unit record (None when no record has the sheet's code)."""
    if row is None:
        return {
            "matched": False,
            "pu_code": fig.get("pu_code"),
            "raw_row_id": None,
            "source_row_number": None,
            "rows": [],
        }
    rows = []
    for field, label in FIELDS:
        a, b = fig.get(field), row.get(field)
        rows.append(
            {"field": field, "label": label, "sheet": a, "data": b, "match": None if a is None or b is None else a == b}
        )
    data_parties = {norm_party(k): (k, v) for k, v in (row.get("party_votes") or {}).items()}
    seen = set()
    for p in fig.get("party_votes") or []:
        key = norm_party(p["party"])
        seen.add(key)
        name, dv = data_parties.get(key, (p["party"], None))
        dv = int(dv) if dv is not None else None
        rows.append(
            {
                "field": f"party:{name}",
                "label": f"{name} votes",
                "sheet": p["votes"],
                "data": dv,
                "match": None if dv is None else p["votes"] == dv,
            }
        )
    for key, (name, dv) in sorted(data_parties.items()):
        if key not in seen and dv not in (None, 0):
            rows.append(
                {"field": f"party:{name}", "label": f"{name} votes", "sheet": None, "data": int(dv), "match": None}
            )
    return {
        "matched": True,
        "pu_code": row.get("pu_code"),
        "raw_row_id": row.get("raw_row_id"),
        "source_row_number": row.get("source_row_number"),
        "rows": rows,
    }


def comparison_check(cmp: dict[str, Any]) -> dict[str, Any]:
    if not cmp["matched"]:
        return _check(
            "sheet_vs_data",
            None,
            "Sheet matches the uploaded results",
            "Not checked: no polling unit in this dataset has the sheet's code. Check the code you typed.",
            None,
        )
    diff = [r for r in cmp["rows"] if r["match"] is False]
    if not diff:
        return _check(
            "sheet_vs_data",
            True,
            "Sheet matches the uploaded results",
            "Every figure that appears in both the sheet and the data is the same.",
            None,
        )
    parts = "; ".join(f"{r['label']}: sheet {r['sheet']:,}, data {r['data']:,}" for r in diff[:6])
    return _check(
        "sheet_vs_data", False, "Sheet matches the uploaded results", f"{len(diff)} figure(s) differ. {parts}.", "high"
    )
