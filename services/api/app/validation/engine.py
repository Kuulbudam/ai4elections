"""Deterministic validation engine (pure: no I/O).

Input: header columns, raw rows (source row number + original cells), a column mapping and an
optional reference list of administrative units. Output: one canonical record per raw row, the
validation issues (each traceable to its source row, column and original cell text), and per-row
statuses with quarantine scoped to the indicators actually affected.

Conventions:
* Cells are never altered; canonical values are parsed copies. Unparseable values become null.
* When a field fails a type/range rule (V003, V004, V005), arithmetic and consistency rules that
  need that field (V006-V009) are not evaluated for the row, so one bad cell is reported once.
* Labels are compared after trimming and collapsing whitespace, case-insensitively.
* Rejected votes and total cast are optional (schema 1.1.0). Rules that need them run only when
  they are mapped. When the mapping says the party columns are a partial list
  (party_columns_complete = false), V008 checks that party votes do not exceed valid votes
  instead of checking that they add up exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.validation.rules import ALL, FIELD_INDICATORS, RULES, SHARE, TURNOUT

SCHEMA_VERSION = "1.1.0"
REQUIRED_TEXT = ["pu_code", "state", "lga", "ward"]
REQUIRED_COUNTS = ["registered_voters", "accredited_voters", "valid_votes"]
OPTIONAL_COUNTS = ["rejected_votes", "total_cast"]
COUNT_FIELDS = REQUIRED_COUNTS + OPTIONAL_COUNTS
INT_RE = re.compile(r"^\+?\d+$")
NEG_RE = re.compile(r"^-\d+$")
MAX_COUNT = 2_000_000_000  # fits PostgreSQL integer


@dataclass
class Issue:
    rule_id: str
    severity: str
    message: str
    source_row_number: int | None = None
    raw_row_id: str | None = None
    source_column: str | None = None
    original_value: str | None = None


@dataclass
class Record:
    raw_row_id: str
    source_row_number: int
    values: dict[str, Any]
    party_votes: dict[str, int | None]
    row_status: str = "eligible"
    excluded_from: list[str] = field(default_factory=list)
    exclusion_reasons: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ValidationResult:
    records: list[Record]
    issues: list[Issue]
    mapping_issues: list[Issue]
    reference_list_used: bool

    def summary(self) -> dict[str, Any]:
        by_rule: dict[str, int] = {}
        rows_by_rule: dict[str, set[int]] = {}
        for i in self.issues:
            by_rule[i.rule_id] = by_rule.get(i.rule_id, 0) + 1
            if i.source_row_number is not None:
                rows_by_rule.setdefault(i.rule_id, set()).add(i.source_row_number)
        status: dict[str, int] = {"eligible": 0, "eligible_with_warnings": 0, "quarantined_from_specific_analysis": 0}
        excluded = {TURNOUT: 0, SHARE: 0}
        for r in self.records:
            status[r.row_status] += 1
            for ind in excluded:
                if ALL in r.excluded_from or ind in r.excluded_from:
                    excluded[ind] += 1
        return {
            "rows": len(self.records),
            "row_status": status,
            "excluded_from": excluded,
            "issues_by_rule": dict(sorted(by_rule.items())),
            "rows_by_rule": {k: len(v) for k, v in sorted(rows_by_rule.items())},
            "reference_list_used": self.reference_list_used,
            "mapping_complete": not self.mapping_issues,
        }


def norm_label(s: str) -> str:
    return " ".join(s.split()).casefold()


def _msg(rule: str, detail: str) -> str:
    return f"{detail}"


def check_mapping(columns: list[str], mapping: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    fields: dict[str, str] = mapping.get("fields", {})
    known = set(columns)
    for f in REQUIRED_TEXT + REQUIRED_COUNTS:
        if f not in fields:
            issues.append(Issue("V001", "error", f"Required field '{f}' is not mapped."))
    if ("latitude" in fields) != ("longitude" in fields):
        issues.append(Issue("V001", "error", "Latitude and longitude must be mapped together."))
    parties = mapping.get("party_columns", [])
    if len(parties) < 2:
        issues.append(Issue("V001", "error", "At least two party vote columns must be mapped."))
    used: dict[str, int] = {}
    for col in [*fields.values(), *(p["source_column"] for p in parties)]:
        used[col] = used.get(col, 0) + 1
        if col not in known:
            issues.append(Issue("V001", "error", f"Column '{col}' does not exist in this file."))
    for col, n in sorted(used.items()):
        if n > 1:
            issues.append(Issue("V002", "error", f"Column '{col}' is assigned to more than one field."))
    codes = [p["party_code"] for p in parties]
    for c in sorted({c for c in codes if codes.count(c) > 1}):
        issues.append(Issue("V002", "error", f"Party code {c} is used twice."))
    return issues


def validate(
    columns: list[str],
    raw_rows: list[tuple[str, int, list[str]]],
    mapping: dict[str, Any],
    admin_units: list[tuple[str, str, str]] | None = None,
) -> ValidationResult:
    mapping_issues = check_mapping(columns, mapping)
    if mapping_issues:
        return ValidationResult([], [], mapping_issues, admin_units is not None)

    idx = {c: i for i, c in enumerate(columns)}
    fields: dict[str, str] = mapping["fields"]
    parties: list[tuple[str, str]] = [(p["party_code"], p["source_column"]) for p in mapping["party_columns"]]
    ref = {tuple(norm_label(x) for x in u) for u in admin_units} if admin_units else None
    tol7 = RULES["V007"]["parameters"]["tolerance"]
    tol8 = RULES["V008"]["parameters"]["tolerance"]
    complete_parties = mapping.get("party_columns_complete", True) is not False

    def cell(cells: list[str], col: str) -> str | None:
        i = idx[col]
        return cells[i] if i < len(cells) else None  # None = cell missing (ragged row)

    records: list[Record] = []
    issues: list[Issue] = []

    for raw_id, row_no, cells in raw_rows:
        row_issues: list[Issue] = []
        bad_fields: set[str] = set()
        values: dict[str, Any] = {}

        def add(rule: str, message: str, col: str | None, value: str | None, fld: str | None = None) -> None:
            row_issues.append(Issue(rule, RULES[rule]["severity"], message, row_no, raw_id, col, value))  # noqa: B023
            if fld:
                bad_fields.add(fld)  # noqa: B023

        for f in REQUIRED_TEXT:
            col = fields[f]
            v = cell(cells, col)
            if v is None or not v.strip():
                values[f] = None
                add("V003", f"{col} is blank.", col, v, f)
            else:
                values[f] = " ".join(v.split())

        def parse_count(f: str, col: str, v: str | None, label: str) -> int | None:
            if v is None or not v.strip():
                add("V003", f"{label} is blank.", col, v, f)
                return None
            s = v.strip()
            if NEG_RE.match(s):
                add("V005", f"{label} is negative ({s}).", col, v, f)
                return None
            if not INT_RE.match(s) or int(s) > MAX_COUNT:
                add("V004", f"{label} is not a whole number: '{v}'.", col, v, f)
                return None
            return int(s)

        for f in COUNT_FIELDS:
            col = fields.get(f)
            values[f] = parse_count(f, col, cell(cells, col), col) if col else None

        party_votes: dict[str, int | None] = {}
        for code, col in parties:
            party_votes[code] = parse_count("party_votes", col, cell(cells, col), col)

        # Coordinates (optional)
        values["latitude"] = values["longitude"] = None
        if "latitude" in fields:
            lat_s, lon_s = cell(cells, fields["latitude"]), cell(cells, fields["longitude"])
            lat_s, lon_s = (lat_s or "").strip(), (lon_s or "").strip()
            if lat_s or lon_s:
                try:
                    lat, lon = float(lat_s), float(lon_s)
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        raise ValueError
                    values["latitude"], values["longitude"] = lat, lon
                except ValueError:
                    add(
                        "V012",
                        f"Invalid coordinates: latitude '{lat_s}', longitude '{lon_s}'.",
                        f"{fields['latitude']} / {fields['longitude']}",
                        f"{lat_s} / {lon_s}",
                    )

        reg, acc, val, rej, tot = (values[f] for f in COUNT_FIELDS)
        has_rej, has_tot = "rejected_votes" in fields, "total_cast" in fields
        ok = lambda *fs: not (set(fs) & bad_fields)  # noqa: E731, B023

        if ok("registered_voters", "accredited_voters") and acc > reg:
            add("V006", f"Accredited voters ({acc}) exceed registered voters ({reg}).",
                fields["accredited_voters"], cell(cells, fields["accredited_voters"]))  # fmt: skip
        if has_tot and has_rej and ok("total_cast", "valid_votes", "rejected_votes") and abs(tot - (val + rej)) > tol7:
            add("V007", f"Total cast ({tot}) does not equal valid ({val}) + rejected ({rej}) = {val + rej}.",
                fields["total_cast"], cell(cells, fields["total_cast"]))  # fmt: skip
        elif has_tot and ok("total_cast", "accredited_voters"):
            if tot > acc:
                add("V007", f"Total cast ({tot}) exceeds accredited voters ({acc}).",
                    fields["total_cast"], cell(cells, fields["total_cast"]))  # fmt: skip
        elif not has_tot and ok("valid_votes", "rejected_votes", "accredited_voters"):
            counted = val + (rej if has_rej else 0)
            if counted > acc:
                what = f"Valid ({val}) + rejected ({rej}) votes" if has_rej else f"Valid votes ({val})"
                add("V007", f"{what} exceed accredited voters ({acc}).",
                    fields["valid_votes"], cell(cells, fields["valid_votes"]))  # fmt: skip
        if ok("valid_votes", "party_votes"):
            s = sum(v for v in party_votes.values() if v is not None)
            if complete_parties and abs(s - val) > tol8:
                add("V008", f"Party votes add up to {s}, but valid votes are {val}.",
                    fields["valid_votes"], cell(cells, fields["valid_votes"]))  # fmt: skip
            elif not complete_parties and s > val + tol8:
                add("V008", f"The mapped parties' votes add up to {s}, more than valid votes ({val}).",
                    fields["valid_votes"], cell(cells, fields["valid_votes"]))  # fmt: skip
        if has_tot and ok("valid_votes", "total_cast") and val > tot:
            add("V009", f"Valid votes ({val}) exceed total cast ({tot}).", fields["valid_votes"],
                cell(cells, fields["valid_votes"]))  # fmt: skip
        if has_tot and has_rej and ok("rejected_votes", "total_cast") and rej > tot:
            add("V009", f"Rejected votes ({rej}) exceed total cast ({tot}).", fields["rejected_votes"],
                cell(cells, fields["rejected_votes"]))  # fmt: skip
        if ok("valid_votes", "party_votes"):
            for code, col in parties:
                pv = party_votes[code]
                if pv is not None and pv > val:
                    add("V009", f"{code} votes ({pv}) exceed valid votes ({val}).", col, cell(cells, col))

        if ref is not None and all(values[f] for f in ("state", "lga", "ward")):
            key = (norm_label(values["state"]), norm_label(values["lga"]), norm_label(values["ward"]))
            if key not in ref:
                add("V011", f"'{values['state']} / {values['lga']} / {values['ward']}' is not in the reference list.",
                    fields["ward"], cell(cells, fields["ward"]))  # fmt: skip

        records.append(Record(raw_id, row_no, values, party_votes))
        issues.extend(row_issues)
        records[-1].exclusion_reasons = [{"rule_id": i.rule_id, "column": i.source_column} for i in row_issues]

    # V010: duplicate PU codes across the version (all rows sharing a code are flagged)
    by_code: dict[str, list[int]] = {}
    for i, r in enumerate(records):
        code = r.values.get("pu_code")
        if code:
            by_code.setdefault(code.casefold(), []).append(i)
    for positions in by_code.values():
        if len(positions) > 1:
            rows_txt = ", ".join(str(records[p].source_row_number) for p in positions)
            for p in positions:
                r = records[p]
                issue = Issue("V010", "error", f"Polling unit code '{r.values['pu_code']}' appears on rows {rows_txt}.",
                              r.source_row_number, r.raw_row_id, fields["pu_code"], r.values["pu_code"])  # fmt: skip
                issues.append(issue)
                r.exclusion_reasons.append({"rule_id": "V010", "column": fields["pu_code"]})

    # Row status and scoped quarantine
    issues_by_row: dict[str, list[Issue]] = {}
    for i in issues:
        issues_by_row.setdefault(i.raw_row_id, []).append(i)
    col_to_field = {c: f for f, c in fields.items()} | {c: "party_votes" for _, c in parties}
    for r in records:
        excl: set[str] = set()
        has_any = False
        for i in issues_by_row.get(r.raw_row_id, []):
            has_any = True
            if i.severity != "error":
                continue
            scope = RULES[i.rule_id]["excludes_from"]
            if scope == ["by_field"]:
                scope = FIELD_INDICATORS.get(col_to_field.get(i.source_column or "", ""), [])
            excl.update(scope)
        if ALL in excl:
            excl = {ALL}
        r.excluded_from = sorted(excl)
        r.row_status = (
            "quarantined_from_specific_analysis" if excl else ("eligible_with_warnings" if has_any else "eligible")
        )
    return ValidationResult(records, issues, [], admin_units is not None)
