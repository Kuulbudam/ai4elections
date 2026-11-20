"""Collation check engine (no database): parsing declared totals, summing polling units, comparing."""

from __future__ import annotations

import pytest

from app.collation import engine as col

CSV = b"""State,LGA,Ward,Registered,Accredited Voters,Total Valid Votes,Rejected,Total Votes Cast,Votes APC,PDP,Notes
Plateau,Jos North,Ward A,300,150,140,5,145,90,50,typed from EC8B
Plateau,Jos North,Ward B,200,100,96,4,100,60,36,
Plateau,Jos North,,500,250,236,9,245,150,86,EC8C
Plateau,,,500,250,236,9,245,150,86,EC8D
Plateau,Jos North,Ward C,1,1,1,1,1,1,1,no units in the data
"""

UNITS = [
    {"pu_code": "1", "state": "Plateau", "lga": "Jos North", "ward": "Ward A", "registered_voters": 100,
     "accredited_voters": 50, "valid_votes": 48, "rejected_votes": 2, "total_cast": 50,
     "party_votes": {"APC": 30, "PDP": 18}},
    {"pu_code": "2", "state": "plateau ", "lga": "JOS  NORTH", "ward": "ward a", "registered_voters": 200,
     "accredited_voters": 100, "valid_votes": 92, "rejected_votes": 3, "total_cast": 95,
     "party_votes": {"APC": 60, "PDP": 32}},
    {"pu_code": "3", "state": "Plateau", "lga": "Jos North", "ward": "Ward B", "registered_voters": 200,
     "accredited_voters": 100, "valid_votes": 96, "rejected_votes": None, "total_cast": 100,
     "party_votes": {"APC": 60, "PDP": 36}},
    {"pu_code": "3", "state": "Plateau", "lga": "Jos North", "ward": "Ward B", "registered_voters": 200,
     "accredited_voters": 100, "valid_votes": 96, "rejected_votes": 4, "total_cast": 100,
     "party_votes": {"APC": 60, "PDP": 36}},
]  # fmt: skip


def run():
    return {(a["level"], a.get("ward") or a.get("lga") or a["state"]): a
            for a in col.compare(col.parse_declared(CSV), col.aggregate(UNITS))}  # fmt: skip


def test_parse_reads_aliases_levels_parties_and_ignores_text_columns():
    d = col.parse_declared(CSV)
    assert [r["level"] for r in d.rows] == ["ward", "ward", "lga", "state", "ward"]
    assert d.parties == ["APC", "PDP"] and d.ignored_columns == ["Notes"]
    first = d.rows[0]
    assert first["figures"] == {"registered_voters": 300, "accredited_voters": 150, "valid_votes": 140,
                                "rejected_votes": 5, "total_cast": 145}  # fmt: skip
    assert first["parties"] == {"APC": 90, "PDP": 50} and d.rows[3]["lga"] is None


def test_matching_ward_lga_state_with_messy_labels():
    r = run()
    a = r[("ward", "Ward A")]
    assert a["units"] == 2 and a["status"] == "match"
    assert {x["field"]: x["data"] for x in a["figures"]}["party:APC"] == 90


def test_incomplete_when_a_unit_lacks_a_figure_and_duplicates_counted_once():
    b = run()[("ward", "Ward B")]
    assert b["units"] == 1 and b["duplicate_units"] == 1, "the repeated polling-unit code is counted once"
    rej = next(x for x in b["figures"] if x["field"] == "rejected_votes")
    assert rej["match"] is None and rej["missing_units"] == 1 and b["status"] == "incomplete"


def test_mismatch_and_unmatched():
    r = run()
    lga = r[("lga", "Jos North")]
    assert lga["status"] == "incomplete"  # rejected votes missing in Ward B makes that figure not checkable
    state = r[("state", "Plateau")]
    assert {x["field"]: x["match"] for x in state["figures"]}["valid_votes"] is True
    assert r[("ward", "Ward C")]["status"] == "unmatched"

    d = col.parse_declared(CSV.replace(b"Ward A,300,150,140,5,145,90,50", b"Ward A,300,150,140,5,145,210,50"))
    a = next(x for x in col.compare(d, col.aggregate(UNITS)) if x["ward"] == "Ward A")
    assert a["status"] == "mismatch" and col.severity(a) == "high"
    diff = next(x for x in a["figures"] if x["field"] == "party:APC")
    assert diff["diff"] == 120 and "APC votes: declared 210, polling units add up to 90 (+120)" in col.describe(a)
    small = col.parse_declared(CSV.replace(b"Ward A,300,150,140,5,145", b"Ward A,301,150,140,5,145"))
    a2 = next(x for x in col.compare(small, col.aggregate(UNITS)) if x["ward"] == "Ward A")
    assert a2["status"] == "mismatch" and col.severity(a2) == "medium"


@pytest.mark.parametrize(
    "data,msg",
    [
        (b"LGA,Ward,Valid\nX,Y,1\n", "State column"),
        (b"State,LGA,Ward,Valid\nPlateau,,,\n", "No usable rows"),
        (b"State,LGA,Ward,Valid\n,,,\n", "no data rows"),
        (b"\x00\x01binary", "binary"),
    ],
)
def test_unusable_files(data, msg):
    with pytest.raises(col.DeclaredError, match=msg):
        col.parse_declared(data)


def test_bad_rows_are_skipped_with_reasons():
    d = col.parse_declared(
        b"State,LGA,Ward,Valid,APC\nPlateau,Jos North,W1,12x,3\nPlateau,,W2,5,5\nPlateau,Jos North,W1,5,5\n"
        b"Plateau,Jos North,W3,1,1\nplateau,jos north,w3,2,2\n"
    )
    reasons = {s["row"]: s["reason"] for s in d.skipped}
    assert "Not whole numbers: valid_votes" in reasons[2] or "Not whole numbers" in reasons[2]
    assert "No LGA" in reasons[3]
    assert "appears twice" in reasons[6]
    assert [r["ward"] for r in d.rows] == ["W1", "W3"]


def test_party_code_and_headers():
    assert (
        col.party_code("Votes APC") == "APC"
        and col.party_code("votes: LP") == "LP"
        and col.party_code("NNPP") == "NNPP"
    )
    assert col.norm_header("Total Votes Cast") == "totalvotescast"
