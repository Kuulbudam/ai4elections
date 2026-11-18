import pytest

from app.ingest.csv_parse import MAX_CELL_CHARS, ParseError, parse_csv


def test_semicolon_bom_and_blank_lines():
    data = "﻿PU Code;Ward;Votes\nA;W1;10\n\n ; ; \nB;W2;=SUM(1,2)\n".encode()
    p = parse_csv(data)
    assert p.delimiter == ";" and p.encoding == "utf-8-bom"
    assert p.columns == ["PU Code", "Ward", "Votes"]
    assert [n for n, _ in p.rows] == [2, 5]  # spreadsheet row numbers, blank lines skipped not renumbered
    assert p.blank_rows_skipped == 2
    assert p.rows[1][1][2] == "=SUM(1,2)"  # formulas are kept as plain text, never evaluated


def test_quoted_fields_with_commas_and_newlines():
    p = parse_csv(b'a,b\n"x, y","line1\nline2"\n3,4\n')
    assert p.rows[0] == (2, ["x, y", "line1\nline2"])
    assert p.rows[1][0] == 3


def test_ragged_rows_are_kept_and_counted():
    p = parse_csv(b"a,b,c\n1,2\n1,2,3,4\n")
    assert len(p.rows) == 2 and p.ragged_rows == 2


@pytest.mark.parametrize(
    "data,code",
    [
        (b"", "EMPTY_FILE"),
        (b"a,b\n\x001,2\n", "BINARY_CONTENT"),
        ("a,b\n1,2\n".encode("utf-16"), "BINARY_CONTENT"),
        (b"a,b\n\xff\xfe1,2\n" + b"x" * 100, "ENCODING_NOT_UTF8"),
        (b"only\n1\n", "HEADER_NOT_FOUND"),
        (b"a,b\n" + b"x" * (MAX_CELL_CHARS + 1) + b",1\n", "CELL_TOO_LONG"),
        (
            (",".join(f"c{i}" for i in range(201)) + "\n" + ",".join("1" * 201) + "\n").encode(),
            "TOO_MANY_COLUMNS",
        ),
    ],
)
def test_rejections(data, code):
    with pytest.raises(ParseError) as e:
        parse_csv(data)
    assert e.value.code == code
