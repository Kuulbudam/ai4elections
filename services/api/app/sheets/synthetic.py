"""Synthetic result-sheet images for tests, live checks and the demo. Not a real INEC form.

Draws a sheet laid out like Form EC8A with the figures typed in, stamped SYNTHETIC on every image so it can
never be mistaken for a real result sheet. `python -m app.sheets.synthetic --row 1 --out sheet.png` writes one
from the synthetic results file.
"""

from __future__ import annotations

import argparse
import csv
import io
import random
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

SYNTHETIC_CSV = Path(__file__).resolve().parents[4] / "data" / "synthetic" / "SYNTHETIC_results_seed2026.csv"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def render(fig: dict[str, Any], width: int = 1240) -> bytes:
    """PNG of a synthetic EC8A-style sheet showing exactly the figures given."""
    parties = fig.get("party_votes") or []
    height = 1100 + 60 * len(parties)
    im = Image.new("RGB", (width, height), (250, 249, 244))
    d = ImageDraw.Draw(im)
    big, mid, small = _font(44), _font(30), _font(24)
    ink, grey = (20, 30, 60), (110, 110, 110)

    d.text((60, 40), "FORM EC8A (SYNTHETIC TEST SHEET)", font=big, fill=ink)
    d.text((60, 100), "STATEMENT OF RESULT OF POLL FROM POLLING UNIT", font=mid, fill=ink)
    d.text((60, 140), "Not a real INEC document. Generated for software testing only.", font=small, fill=grey)
    y = 200
    for label, key in (("State", "state"), ("LGA", "lga"), ("Registration Area / Ward", "ward"),
                       ("Polling Unit", "polling_unit_name"), ("Polling Unit Code", "pu_code")):  # fmt: skip
        d.text((60, y), f"{label}:", font=mid, fill=ink)
        d.text((520, y), str(fig.get(key) or ""), font=mid, fill=ink)
        d.line((510, y + 38, width - 60, y + 38), fill=grey, width=2)
        y += 56
    y += 20
    rows = [
        ("1. Number of voters on the register", fig.get("registered_voters")),
        ("2. Number of accredited voters", fig.get("accredited_voters")),
        ("3. Number of rejected ballots", fig.get("rejected_votes")),
        ("4. Total valid votes", fig.get("valid_votes")),
        ("5. Total used ballot papers (votes cast)", fig.get("total_cast")),
    ]
    for label, val in rows:
        d.rectangle((60, y, width - 60, y + 54), outline=grey, width=2)
        d.text((76, y + 12), label, font=mid, fill=ink)
        d.text((width - 300, y + 2), "" if val is None else str(val), font=big, fill=(10, 40, 140))
        y += 54
    y += 40
    d.text((60, y), "Party", font=mid, fill=ink)
    d.text((width - 300, y), "Votes (figures)", font=mid, fill=ink)
    y += 50
    for p in parties:
        d.rectangle((60, y, width - 60, y + 54), outline=grey, width=2)
        d.text((76, y + 12), str(p["party"]), font=mid, fill=ink)
        d.text((width - 300, y + 2), str(p["votes"]), font=big, fill=(10, 40, 140))
        y += 60
    stamp = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(stamp).text((width // 2 - 330, height // 2 - 60), "SYNTHETIC", font=_font(150),
                               fill=(200, 40, 40, 60))  # fmt: skip
    im = Image.alpha_composite(im.convert("RGBA"), stamp).convert("RGB")
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def photo_like(png: bytes, seed: int = 7) -> bytes:
    """Make a clean synthetic sheet look like a phone photo: tilted, soft, unevenly lit, heavily compressed."""
    rnd = random.Random(seed)  # noqa: S311 - test images, not security
    with Image.open(io.BytesIO(png)) as src:
        im = src.convert("RGB")
    im = im.rotate(rnd.uniform(-4, 4), resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(90, 80, 70))
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    shade = Image.linear_gradient("L").resize(im.size).rotate(rnd.choice([0, 90, 180, 270]))
    im = Image.composite(im, ImageEnhance.Brightness(im).enhance(0.6), shade)
    im = im.resize((int(im.width * 0.8), int(im.height * 0.8)))
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=45)
    return out.getvalue()


def figures_from_csv(row_number: int, path: Path = SYNTHETIC_CSV) -> dict[str, Any]:
    """Figures for the n-th data row (1-based) of the synthetic results file."""
    with path.open(newline="", encoding="utf-8") as f:
        for i, r in enumerate(csv.DictReader(f), start=1):
            if i == row_number:
                parties = [{"party": k.removeprefix("Votes ").strip(), "votes": int(v)}
                           for k, v in r.items() if k.startswith("Votes ") and v]  # fmt: skip
                return {"pu_code": r["PU Code"], "state": r["State"], "lga": r["LGA"], "ward": r["Ward"],
                        "polling_unit_name": f"Synthetic unit {row_number}",
                        "registered_voters": int(r["Registered Voters"]),
                        "accredited_voters": int(r["Accredited Voters"]),
                        "rejected_votes": int(r["Rejected Votes"]), "valid_votes": int(r["Valid Votes"]),
                        "total_cast": int(r["Total Votes Cast"]), "party_votes": parties}  # fmt: skip
    raise ValueError(f"row {row_number} not found")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--row", type=int, default=1)
    ap.add_argument("--add", type=int, default=0, help="add this many votes to the first party (makes a mismatch)")
    ap.add_argument("--photo", action="store_true", help="make it look like a phone photo (JPEG)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    fig = figures_from_csv(a.row)
    if a.add and fig["party_votes"]:
        fig["party_votes"][0]["votes"] += a.add
    png = render(fig)
    Path(a.out).write_bytes(photo_like(png) if a.photo else png)


if __name__ == "__main__":
    main()
