# ruff: noqa: E501  (human-readable rule texts are kept on one line)
"""Versioned validation rule catalogue (master prompt 5.5). Single source of truth.

A rule's behaviour never changes under the same (rule_id, version). To change a rule, bump its
version; stored issues keep the version they were produced with.
"""

from __future__ import annotations

from typing import Any

RULESET_VERSION = "1.2.0"  # 1.1.0: optional rejected/total cast; partial party lists (ADR 0007). 1.2.0: V013, V014
TURNOUT = "turnout_rate"
SHARE = "leading_party_share"
ALL = "all"

# Which indicator each canonical field feeds. Used to scope quarantine to affected calculations.
FIELD_INDICATORS: dict[str, list[str]] = {
    "pu_code": [ALL],
    "state": [ALL],
    "lga": [ALL],
    "ward": [ALL],
    "registered_voters": [TURNOUT],
    "accredited_voters": [TURNOUT],
    "valid_votes": [SHARE],
    "party_votes": [SHARE],
    "rejected_votes": [],
    "total_cast": [],
    "latitude": [],
    "longitude": [],
}

RULES: dict[str, dict[str, Any]] = {
    "V001": {
        "severity": "error",
        "classification": "structural",
        "description": "Every required canonical field must be mapped to an existing column; latitude and longitude must be mapped together.",
        "affected_fields": ["mapping"],
        "remediation": "Open the column mapping and assign a column to each required field.",
        "excludes_from": [ALL],
    },
    "V002": {
        "severity": "error",
        "classification": "structural",
        "description": "A source column may be assigned to only one field, and each party code may be used once.",
        "affected_fields": ["mapping"],
        "remediation": "Assign each column to a single field in the column mapping.",
        "excludes_from": [ALL],
    },
    "V003": {
        "severity": "error",
        "classification": "completeness",
        "description": "A required value is missing (blank cell or missing cell).",
        "affected_fields": [
            "pu_code",
            "state",
            "lga",
            "ward",
            "registered_voters",
            "accredited_voters",
            "valid_votes",
            "rejected_votes",
            "total_cast",
            "party_votes",
        ],
        "remediation": "Fill in the missing value from the source result sheet and upload a new version.",
        "excludes_from": ["by_field"],
    },  # fmt: skip
    "V004": {
        "severity": "error",
        "classification": "structural",
        "description": "A count is not a whole number (text, decimals, thousands separators or symbols).",
        "affected_fields": [
            "registered_voters",
            "accredited_voters",
            "valid_votes",
            "rejected_votes",
            "total_cast",
            "party_votes",
        ],
        "remediation": "Correct the value to a whole number as written on the source result sheet.",
        "excludes_from": ["by_field"],
    },  # fmt: skip
    "V005": {
        "severity": "error",
        "classification": "structural",
        "description": "A count is negative.",
        "affected_fields": [
            "registered_voters",
            "accredited_voters",
            "valid_votes",
            "rejected_votes",
            "total_cast",
            "party_votes",
        ],
        "remediation": "Counts cannot be negative. Check the source sheet for a sign or transcription error.",
        "excludes_from": ["by_field"],
    },  # fmt: skip
    "V006": {
        "severity": "error",
        "classification": "consistency",
        "description": "Accredited voters exceed registered voters.",
        "affected_fields": ["accredited_voters", "registered_voters"],
        "remediation": "Check both figures against the source result sheet and the voter register.",
        "excludes_from": [TURNOUT],
    },
    "V007": {
        "severity": "error",
        "classification": "arithmetic",
        "description": "Vote reconciliation failed: total votes cast must equal valid plus rejected votes, and votes counted must not exceed accredited voters. Where total cast is not supplied, valid votes (plus rejected votes, if supplied) are compared with accredited voters.",
        "affected_fields": ["total_cast", "valid_votes", "rejected_votes", "accredited_voters"],
        "remediation": "Re-add valid and rejected votes from the source sheet and compare with total cast and accreditation.",
        "excludes_from": [SHARE],
        "parameters": {"tolerance": 0},
    },
    "V008": {
        "severity": "error",
        "classification": "arithmetic",
        "description": "Party votes do not add up to valid votes. Where the file lists only some parties, the listed parties' votes must not exceed valid votes.",
        "affected_fields": ["party_votes", "valid_votes"],
        "remediation": "Re-add the party votes from the source sheet; check for a missing or misread party column.",
        "excludes_from": [SHARE],
        "parameters": {"tolerance": 0},
    },
    "V009": {
        "severity": "error",
        "classification": "consistency",
        "description": "A count exceeds the count it is part of (valid or rejected votes above total cast, or a party above valid votes).",
        "affected_fields": ["valid_votes", "rejected_votes", "total_cast", "party_votes"],
        "remediation": "Check the figures against the source sheet; a column may be shifted or misread.",
        "excludes_from": [SHARE],
    },
    "V010": {
        "severity": "error",
        "classification": "identity",
        "description": "The same polling unit code appears on more than one row.",
        "affected_fields": ["pu_code"],
        "remediation": "Keep one row per polling unit; check whether the rows are duplicates or a mistyped code.",
        "excludes_from": [ALL],
    },
    "V011": {
        "severity": "warning",
        "classification": "geography",
        "description": "The state / LGA / ward combination is not in the project's reference list of administrative units.",
        "affected_fields": ["state", "lga", "ward"],
        "remediation": "Correct the spelling to match the reference list, or add the unit to the reference list if it is genuinely missing.",
        "excludes_from": [],
    },
    "V012": {
        "severity": "warning",
        "classification": "geography",
        "description": "Coordinates are invalid: only one of latitude/longitude, not a number, or out of range.",
        "affected_fields": ["latitude", "longitude"],
        "remediation": "Supply both coordinates as decimal degrees, or leave both blank.",
        "excludes_from": [],
    },
    "V013": {
        "severity": "warning",
        "classification": "pattern",
        "description": "Every party with votes has a round number (a multiple of 10), for three or more parties. Real counts are rarely all round.",
        "affected_fields": ["party_votes"],
        "remediation": "Compare the party scores with the result sheet; round figures can be estimates written in place of a count.",
        "excludes_from": [],
        "parameters": {"multiple": 10, "min_parties": 3},
    },
    "V014": {
        "severity": "warning",
        "classification": "pattern",
        "description": "The party scores are exactly the same as on an earlier row (another polling unit) in the same file (3 or more parties with votes, 100 or more votes).",
        "affected_fields": ["party_votes"],
        "remediation": "Check both units' result sheets; the figures may have been copied from one unit to the other.",
        "excludes_from": [],
        "parameters": {"min_total_votes": 100, "min_parties": 3},
    },
}

for _rid, _r in RULES.items():
    _r.setdefault("parameters", {})
    _r["rule_id"] = _rid
    _r["version"] = RULESET_VERSION


def definitions() -> list[dict[str, Any]]:
    return [RULES[k] for k in sorted(RULES)]
