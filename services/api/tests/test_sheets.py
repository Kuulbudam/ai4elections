"""Result-sheet reader: photo intake, parsing the AI's reply, and the checks (no database)."""

from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image

from app.assistant.llm import LLMError
from app.sheets import checks as chk
from app.sheets.image import ImageRejected, clean, decode_base64
from app.sheets.read import empty_reading, parse_reading, read_sheet
from app.sheets.synthetic import figures_from_csv, render


def _jpeg_with_gps() -> bytes:
    im = Image.new("RGB", (800, 600), (240, 240, 240))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make
    exif[0x8825] = {1: "N", 2: (9.0, 55.0, 0.0)}  # GPS IFD
    out = io.BytesIO()
    im.save(out, format="JPEG", exif=exif)
    return out.getvalue()


def test_clean_drops_location_and_camera_metadata():
    raw = _jpeg_with_gps()
    assert b"PhoneMaker" in raw
    img = clean(raw)
    assert b"PhoneMaker" not in img.jpeg and b"Exif" not in img.jpeg[:200]
    with Image.open(io.BytesIO(img.jpeg)) as out:
        assert out.format == "JPEG" and not out.getexif()
    assert img.original_sha256 != img.sha256 and len(img.sha256) == 64


def test_clean_converts_png_and_limits_size():
    img = clean(render(figures_from_csv(1), width=2600))
    with Image.open(io.BytesIO(img.jpeg)) as out:
        assert out.format == "JPEG" and max(out.size) <= 2000


@pytest.mark.parametrize(
    "raw,msg",
    [
        (b"%PDF-1.7 not an image" * 20, "not a photo"),
        (b"\x89PNG\r\n\x1a\n" + b"\0" * 200, "not a photo"),
    ],
)
def test_clean_rejects_files_that_are_not_photos(raw, msg):
    with pytest.raises(ImageRejected, match=msg):
        clean(raw)


def test_clean_rejects_tiny_and_unusual_formats():
    out = io.BytesIO()
    Image.new("RGB", (120, 90)).save(out, format="PNG")
    with pytest.raises(ImageRejected, match="too small"):
        clean(out.getvalue())
    gif = io.BytesIO()
    Image.new("RGB", (400, 400)).save(gif, format="GIF")
    with pytest.raises(ImageRejected, match="JPEG, PNG or WebP"):
        clean(gif.getvalue())


def test_decode_base64_accepts_data_url_and_refuses_junk_and_huge():
    raw = _jpeg_with_gps()
    assert decode_base64("data:image/jpeg;base64," + base64.b64encode(raw).decode()) == raw
    with pytest.raises(ImageRejected, match="base64"):
        decode_base64("not base64 at all!!")
    with pytest.raises(ImageRejected, match="too large"):
        decode_base64(base64.b64encode(b"\0" * 4_600_000).decode())


GOOD = {
    "legible": True,
    "notes": "Clear photo.",
    "pu_code": {"value": "31/04/01/003", "confidence": "high"},
    "state": {"value": "Plateau", "confidence": "high"},
    "lga": {"value": "Jos North", "confidence": "medium"},
    "ward": {"value": None, "confidence": "low"},
    "polling_unit_name": {"value": "LGEA Primary School", "confidence": "high"},
    "registered_voters": {"value": "1,305", "confidence": "high"},
    "accredited_voters": {"value": 329, "confidence": "high"},
    "rejected_votes": {"value": 6, "confidence": "medium"},
    "valid_votes": {"value": 319, "confidence": "high"},
    "total_cast": {"value": 325.0, "confidence": "HIGH"},
    "party_votes": [
        {"party": "apc", "votes": 70, "confidence": "high"},
        {"party": "PDP", "votes": "32", "confidence": "sure"},
        {"party": "LP", "votes": None, "confidence": "low"},
        {"party": "APC", "votes": 99, "confidence": "high"},
    ],
}


def test_parse_reading_coerces_and_keeps_confidence():
    r = parse_reading(json.dumps(GOOD))
    f = r["figures"]
    assert r["status"] == "read" and r["legible"] is True
    assert f["registered_voters"] == 1305 and f["total_cast"] == 325 and f["ward"] is None
    assert f["party_votes"] == [{"party": "APC", "votes": 70}, {"party": "PDP", "votes": 32}]  # LP had no score
    assert r["confidence"]["total_cast"] == "high" and r["confidence"]["party:PDP"] == "low"  # unknown level → low
    assert "ward" not in r["confidence"]


def test_parse_reading_handles_fences_and_rejects_nonsense_numbers():
    bad = dict(GOOD, valid_votes={"value": -5}, rejected_votes={"value": True}, accredited_voters={"value": 2.5},
               registered_voters={"value": "99999999999"})  # fmt: skip
    r = parse_reading("Here you go:\n```json\n" + json.dumps(bad) + "\n```")
    f = r["figures"]
    assert f["valid_votes"] is None and f["rejected_votes"] is None and f["accredited_voters"] is None
    assert f["registered_voters"] is None and f["total_cast"] == 325


def test_parse_reading_unreadable_cases():
    assert parse_reading("I cannot see a sheet")["status"] == "unreadable"
    r = parse_reading(json.dumps({"legible": False, "notes": "This is a photo of a car."}))
    assert r["status"] == "unreadable" and r["notes"] == "This is a photo of a car."
    assert parse_reading("[1, 2]")["status"] == "unreadable"


class Boom:
    name = "boom"

    def read_image(self, prompt, jpeg):
        raise LLMError("The sheet reader is busy.")


def test_read_sheet_never_raises_and_falls_back_to_typing():
    assert read_sheet(None, b"x")["status"] == "no_model"
    r = read_sheet(Boom(), b"x")
    assert r["status"] == "failed" and "busy" in r["notes"] and r["figures"]["party_votes"] == []
    assert empty_reading("no_model")["figures"]["valid_votes"] is None


FIG = {"registered_voters": 1305, "accredited_voters": 329, "rejected_votes": 6, "valid_votes": 319,
       "total_cast": 325, "party_votes": [{"party": "PA", "votes": 200}, {"party": "PB", "votes": 119}]}  # fmt: skip


def _by_id(results):
    return {c["id"]: c for c in results}


def test_arithmetic_all_pass():
    r = _by_id(chk.arithmetic(FIG))
    assert all(c["ok"] is True and c["severity"] is None for c in r.values())


def test_arithmetic_failures_and_missing():
    bad = dict(FIG, party_votes=[{"party": "PA", "votes": 215}, {"party": "PB", "votes": 119}], total_cast=400,
               accredited_voters=1400)  # fmt: skip
    r = _by_id(chk.arithmetic(bad))
    assert r["sheet_party_sum"]["ok"] is False and "334" in r["sheet_party_sum"]["detail"]
    assert r["sheet_total"]["ok"] is False and r["sheet_total"]["severity"] == "high"
    assert r["sheet_over_accredited"]["ok"] is True
    assert r["sheet_over_registered"]["ok"] is False and r["sheet_over_registered"]["severity"] == "very_high"
    over = _by_id(chk.arithmetic(dict(FIG, accredited_voters=300)))
    assert over["sheet_over_accredited"]["ok"] is False
    missing = _by_id(chk.arithmetic({"party_votes": []}))
    assert all(c["ok"] is None and "Not checked" in c["detail"] for c in missing.values())


def test_compare_with_data_row():
    row = {"pu_code": "SY-01", "raw_row_id": "r1", "source_row_number": 2, "registered_voters": 1305,
           "accredited_voters": 329, "rejected_votes": 6, "valid_votes": 319, "total_cast": 325,
           "party_votes": {"Votes PA": 185, "PB": 119, "PC": 15}}  # fmt: skip
    fig = dict(FIG, party_votes=[{"party": "PB", "votes": 119}, {"party": "PA", "votes": 200}])
    cmp = chk.compare(fig, {**row, "party_votes": {"PA": 185, "PB": 119, "PC": 15}})
    rows = {r["field"]: r for r in cmp["rows"]}
    assert cmp["matched"] and rows["valid_votes"]["match"] is True
    assert rows["party:PA"]["match"] is False and rows["party:PA"]["data"] == 185
    assert rows["party:PC"]["sheet"] is None and rows["party:PC"]["match"] is None
    c = chk.comparison_check(cmp)
    assert c["ok"] is False and "PA votes: sheet 200, data 185" in c["detail"]
    same = chk.compare(dict(FIG, party_votes=[{"party": "PA", "votes": 185}]), {**row, "party_votes": {"PA": 185}})
    assert chk.comparison_check(same)["ok"] is True
    none = chk.comparison_check(chk.compare(FIG, None))
    assert none["ok"] is None and "no polling unit" in none["detail"]


@pytest.mark.parametrize("code", ["31/04/01/003", "31-04-01-003", " 31 04 01 003 ", "31.04.01.003"])
def test_norm_code(code):
    assert chk.norm_code(code) == "31/04/01/003"
