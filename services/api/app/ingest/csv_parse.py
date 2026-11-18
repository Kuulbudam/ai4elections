"""Safe CSV parsing for uploaded result files.

Pure function, no I/O. Never evaluates formulas, never guesses types (every cell stays text),
never drops a non-blank row. Fails with a stable error code instead of guessing.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

MAX_BYTES = 25 * 1024 * 1024
MAX_COLUMNS = 200
MAX_ROWS = 200_000
MAX_CELL_CHARS = 1_000
DELIMITERS = ",;\t|"


class ParseError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ParsedCSV:
    columns: list[str]
    rows: list[tuple[int, list[str]]]  # (source_row_number, cells); header is row 1
    delimiter: str
    encoding: str
    blank_rows_skipped: int = 0
    ragged_rows: int = 0
    warnings: list[str] = field(default_factory=list)

    def header_meta(self) -> dict:
        return {
            "columns": self.columns,
            "delimiter": self.delimiter,
            "encoding": self.encoding,
            "blank_rows_skipped": self.blank_rows_skipped,
            "ragged_rows": self.ragged_rows,
        }


def check_upload_prefix(head: bytes) -> None:
    """Cheap check at finalize time on the first bytes of the stored file."""
    if b"\x00" in head:
        raise ParseError("BINARY_CONTENT", "The file contains binary data; upload a CSV text file.")
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character may be cut at the end of the sample; only fail if the error is earlier.
        if exc.start < len(head) - 4:
            raise ParseError("ENCODING_NOT_UTF8", "The file is not UTF-8 text. Save it as CSV UTF-8.") from exc


def _looks_numeric(s: str) -> bool:
    try:
        float(s.replace(",", ""))
        return True
    except ValueError:
        return False


def parse_csv(data: bytes) -> ParsedCSV:
    if len(data) > MAX_BYTES:
        raise ParseError("FILE_TOO_LARGE", "The file is larger than 25 MB.")
    if not data.strip():
        raise ParseError("EMPTY_FILE", "The file is empty.")
    check_upload_prefix(data[:65536])
    if b"\x00" in data:
        raise ParseError("BINARY_CONTENT", "The file contains binary data; upload a CSV text file.")
    try:
        text = data.decode("utf-8-sig")
        encoding = "utf-8-bom" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    except UnicodeDecodeError as exc:
        raise ParseError("ENCODING_NOT_UTF8", "The file is not UTF-8 text. Save it as CSV UTF-8.") from exc

    sample = text[:32768]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=DELIMITERS).delimiter
    except csv.Error:
        delimiter = ","
    # The sniffer can be fooled by a delimiter that appears inside values; prefer the one that splits
    # the first line into the most columns among the allowed set.
    first_line = sample.splitlines()[0] if sample else ""
    best = max(DELIMITERS, key=lambda d: first_line.count(d))
    if first_line.count(best) > first_line.count(delimiter):
        delimiter = best

    csv.field_size_limit(MAX_CELL_CHARS * 4)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    columns: list[str] | None = None
    rows: list[tuple[int, list[str]]] = []
    blank = 0
    ragged = 0
    record_no = 0
    try:
        for record in reader:
            record_no += 1
            if not any(cell.strip() for cell in record):
                blank += 1
                continue
            if any(len(cell) > MAX_CELL_CHARS for cell in record):
                raise ParseError(
                    "CELL_TOO_LONG", f"Row {record_no} has a cell longer than {MAX_CELL_CHARS} characters."
                )
            if columns is None:
                columns = _validate_header(record, record_no)
                continue
            if len(record) != len(columns):
                ragged += 1
            rows.append((record_no, record))
            if len(rows) > MAX_ROWS:
                raise ParseError("TOO_MANY_ROWS", f"The file has more than {MAX_ROWS:,} data rows.")
    except csv.Error as exc:
        raise ParseError("MALFORMED_CSV", f"The file is not valid CSV near row {record_no + 1}.") from exc
    if columns is None:
        raise ParseError("HEADER_NOT_FOUND", "No header row was found.")
    if not rows:
        raise ParseError("NO_DATA_ROWS", "The file has a header but no data rows.")
    parsed = ParsedCSV(columns, rows, delimiter, encoding, blank, ragged)
    if ragged:
        parsed.warnings.append(f"{ragged} row(s) have a different number of cells than the header.")
    return parsed


def _validate_header(record: list[str], row_no: int) -> list[str]:
    cols = [c.strip() for c in record]
    if len(cols) < 2:
        raise ParseError("HEADER_NOT_FOUND", "The first row must contain at least two column names.")
    if len(cols) > MAX_COLUMNS:
        raise ParseError("TOO_MANY_COLUMNS", f"The file has more than {MAX_COLUMNS} columns.")
    if any(not c for c in cols):
        raise ParseError("HEADER_EMPTY_COLUMN", f"Row {row_no} (header) has an empty column name.")
    if sum(_looks_numeric(c) for c in cols) > len(cols) / 2:
        raise ParseError("HEADER_NOT_FOUND", "The first row looks like data, not column names. Add a header row.")
    lowered = [c.lower() for c in cols]
    dupes = sorted({c for c in cols if lowered.count(c.lower()) > 1})
    if dupes:
        raise ParseError("HEADER_DUPLICATE_COLUMNS", f"Duplicate column names: {', '.join(dupes)}.")
    return cols
