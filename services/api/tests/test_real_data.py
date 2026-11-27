"""The CCIJ converter on a tiny hand-made sample (no CCIJ data is committed)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "real_data"))

import prepare_ccij  # noqa: E402

from app.ingest.csv_parse import parse_csv  # noqa: E402
from app.validation.engine import validate  # noqa: E402

VOTER = b"""Accredit_mark,registered_use,Registered_num,Accredited_num,polling_unit_code,status,APC,PDP,LP,NNPP,total_use
266.0,900,900.0,266.0,31/01/01/002,exist and not blur,60.0,66.0,83.0,0.0,211.0
,500,500.0,120.0,31/01/01/003,blurred,,,,,
99.0,300,300.0,99.0,31/01/01/004,exist and not blur,50.0,30.0,20.0,0.0,100.0
10.0,50,50.0,10.0,20/01/01/001,exist and not blur,5.0,5.0,0.0,0.0,10.0
"""
INFO = b"""polling_unit_code,status,state_code,lga_code,ward_code,pu_code,lat,lng,state_name,lga_name,ward_name,unit_name,Document Name,id,URL
31/01/01/002,x,31,1,1,2,9.5,8.8,PLATEAU,BARIKIN LADI,BARAKIN LADI,ST. MARKS PRIM. SCH. I,d,1,https://example.org/a
31/01/01/003,x,31,1,1,3,,,PLATEAU,BARIKIN LADI,BARAKIN LADI,MARKET SQUARE,d,2,https://example.org/b
31/01/01/004,x,31,1,1,4,9.6,8.9,PLATEAU,BARIKIN LADI,BARAKIN LADI,VILLAGE HALL,d,3,https://example.org/c
20/01/01/001,x,20,1,1,1,,,KANO,X,Y,Z,d,4,https://example.org/d
"""


def test_build_filters_state_keeps_blanks_and_strips_dot_zero():
    rows, ref, stats = prepare_ccij.build(VOTER, INFO, "PLATEAU")
    assert [r[0] for r in rows] == ["31/01/01/002", "31/01/01/003", "31/01/01/004"]
    assert rows[0][5:12] == ["900", "266", "211", "60", "66", "83", "0"]
    assert rows[1][7:12] == ["", "", "", "", ""]  # unreadable sheet stays blank, never zero
    assert ref == [["PLATEAU", "BARIKIN LADI", "BARAKIN LADI"]]
    assert stats["valid votes blank"] == 1 and stats["rows"] == 3


def test_converted_file_validates_with_partial_mapping():
    rows, ref, _ = prepare_ccij.build(VOTER, INFO, "PLATEAU")
    text = ",".join(prepare_ccij.HEADER) + "\n" + "\n".join(",".join(f'"{c}"' for c in r) for r in rows) + "\n"
    parsed = parse_csv(text.encode())
    mapping = {
        "schema_version": "1.1.0",
        "fields": dict(zip(
            ["pu_code", "state", "lga", "ward", "registered_voters", "accredited_voters", "valid_votes"],
            ["PU Code", "State", "LGA", "Ward", "Registered Voters", "Accredited Voters", "Valid Votes"],
            strict=True,
        )),
        "party_columns": [{"source_column": f"Votes {p}", "party_code": p} for p in prepare_ccij.PARTIES],
        "party_columns_complete": False,
    }  # fmt: skip
    res = validate(parsed.columns, [(f"r{n}", n, c) for n, c in parsed.rows], mapping, [tuple(u) for u in ref])
    by_row = {}
    for i in res.issues:
        by_row.setdefault(i.source_row_number, set()).add(i.rule_id)
    assert by_row == {3: {"V003"}, 4: {"V007", "V013"}}  # blank sheet; 100 valid votes > 99 accredited, all round
