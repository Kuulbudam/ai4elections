"""How well does the sheet reader read REAL result sheets? (founder request, 20 Nov)

Real Form EC8A photos from INEC's IReV (2023 presidential election, Plateau), as archived on DocumentCloud by the
CCIJ/VEZA project, which also transcribed each sheet's party votes and total valid votes. For a fixed random sample
of sheets this script fetches the page image, runs the platform's own reader on it in memory, and compares figures.

No image is written to disk or uploaded anywhere: each is fetched, read and dropped. Only polling-unit codes and
vote figures (already public) are printed. The transcription is itself machine-read, so a disagreement is not
always the reader's mistake; disagreements are listed for a person to look at.

Needs LLM_API_KEY. Usage: python scripts/real_sheet_benchmark.py --clear 20 --blurred 5
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import random
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assistant.llm import DEFAULT_BASE_URL  # noqa: E402
from app.sheets.image import clean  # noqa: E402
from app.sheets.read import OpenAICompatibleVision, choose_vision_model, read_sheet  # noqa: E402

COMMIT = "e38e3bc9a22ac005564a9e95b4e846208a594522"
RAW = f"https://raw.githubusercontent.com/anndawn/Nigeria-2023-election/{COMMIT}"
PARTIES = ["APC", "PDP", "LP", "NNPP"]
DC_API = "https://api.www.documentcloud.org/api/documents/{}/"


def num(v: str) -> int | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) and f >= 0 else None


def load(http: httpx.Client) -> list[dict]:
    pu = list(csv.DictReader(io.StringIO(http.get(f"{RAW}/AllPollingUnitsInfo.csv").text)))
    vi = {r["polling_unit_code"]: r for r in csv.DictReader(io.StringIO(http.get(f"{RAW}/voter_info.csv").text))}
    rows = []
    for p in pu:
        if not p["polling_unit_code"].startswith("31/") or not p["id"]:
            continue
        v = vi.get(p["polling_unit_code"])
        if not v:
            continue
        truth = {k: num(v[k]) for k in PARTIES} | {"valid_votes": num(v["total_use"])}
        if p["status"] == "exist and not blur" and any(x is None for x in truth.values()):
            continue
        if p["status"] != "blurred" and p["status"] != "exist and not blur":
            continue
        rows.append({"code": p["polling_unit_code"], "status": p["status"], "doc": str(int(float(p["id"]))),
                     "truth": truth, "registered": num(v["registered_use"]),
                     "accredited": num(v["Accredited_num"])})  # fmt: skip
    return rows


def page_image(http: httpx.Client, doc_id: str) -> bytes:
    meta = http.get(DC_API.format(doc_id)).json()
    base = meta.get("asset_url", "https://s3.documentcloud.org/")
    for size in ("xlarge", "large"):
        r = http.get(f"{base}documents/{doc_id}/pages/{meta['slug']}-p1-{size}.gif")
        if r.status_code == 200 and r.content:
            with Image.open(io.BytesIO(r.content)) as im:
                out = io.BytesIO()
                im.convert("RGB").save(out, format="PNG")
                return out.getvalue()
    raise RuntimeError(f"no page image for document {doc_id}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", type=int, default=20)
    ap.add_argument("--blurred", type=int, default=5)
    ap.add_argument("--pace", type=float, default=40.0, help="seconds between AI calls (free tier ~8k tokens/min)")
    a = ap.parse_args()
    key = os.environ["LLM_API_KEY"].strip()
    model = OpenAICompatibleVision(key, DEFAULT_BASE_URL, choose_vision_model(key, DEFAULT_BASE_URL) or "")
    http = httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "AI4Elections research benchmark"})
    rows = load(http)
    rnd = random.Random(2026)  # noqa: S311 - a fixed, repeatable sample, not security
    pool_clear = [r for r in rows if r["status"] == "exist and not blur"]
    pool_blur = [r for r in rows if r["status"] == "blurred"]  # no transcription: measure caution, not accuracy
    clear = rnd.sample(pool_clear, min(a.clear, len(pool_clear)))
    blurred = rnd.sample(pool_blur, min(a.blurred, len(pool_blur)))
    print(f"Model {model.name}; {len(rows)} Plateau sheets with a full transcription; sample {a.clear} clear + "
          f"{a.blurred} blurred", flush=True)  # fmt: skip

    results = []
    for i, r in enumerate(clear + blurred):
        if i:
            time.sleep(a.pace)
        try:
            img = clean(page_image(http, r["doc"]))
            reading = read_sheet(model, img.jpeg)
        except Exception as e:  # noqa: BLE001 - a benchmark keeps going and reports the failure
            results.append({**r, "error": str(e)[:120]})
            print(f"{r['code']}: ERROR {str(e)[:120]}", flush=True)
            continue
        fig = reading["figures"]
        if r["status"] == "blurred":
            conf = reading.get("confidence", {})
            sure = sum(1 for v in conf.values() if v == "high")
            results.append({**r, "blur": {"status": reading["status"], "figures": len(conf), "sure": sure,
                                          "legible": reading.get("legible")}})  # fmt: skip
            print(f"{r['code']} [blurred] reading={reading['status']} legible={reading.get('legible')}: "
                  f"{len(conf)} figures given, {sure} marked sure | note: {(reading.get('notes') or '')[:140]}",
                  flush=True)  # fmt: skip
            continue
        got = {"".join(ch for ch in p["party"].upper() if ch.isalnum()): p["votes"] for p in fig.get("party_votes", [])}
        read = {k: got.get(k) for k in PARTIES} | {"valid_votes": fig.get("valid_votes")}
        conf = reading.get("confidence", {})
        cells = []
        for k, t in r["truth"].items():
            v = read[k]
            c = conf.get("valid_votes" if k == "valid_votes" else f"party:{k}", "missing" if v is None else "low")
            cells.append({"field": k, "truth": t, "read": v, "ok": v == t, "confidence": c})
        results.append({**r, "status_read": reading["status"], "cells": cells,
                        "code_read": fig.get("pu_code"), "reg_read": fig.get("registered_voters"),
                        "acc_read": fig.get("accredited_voters")})  # fmt: skip
        bad = [f"{c['field']} read {c['read']} vs {c['truth']} ({c['confidence']})" for c in cells if not c["ok"]]
        print(f"{r['code']} [{r['status']}] reading={reading['status']} parties={len(got)}: "
              f"{sum(c['ok'] for c in cells)}/{len(cells)} {'; '.join(bad)}"
              f"{' | note: ' + (reading.get('notes') or '')[:120] if bad else ''}", flush=True)  # fmt: skip

    report(results)
    return 0


def report(results: list[dict]) -> None:
    lines = ["## Real result-sheet benchmark (IReV EC8A, 2023 presidential, Plateau)", ""]
    for label in ("exist and not blur",):
        rs = [r for r in results if "cells" in r]
        cells = [c for r in rs for c in r["cells"]]
        if not cells:
            continue
        ok = sum(c["ok"] for c in cells)
        wrong = [c for c in cells if not c["ok"]]
        flagged = sum(c["confidence"] in ("low", "medium", "missing") for c in wrong)
        sheets_all = sum(all(c["ok"] for c in r["cells"]) for r in rs)
        lines += [
            f"**{'Clear' if label != 'other' else 'Harder (blurred or crowd-read)'} sheets ({len(rs)})**: {ok}/{len(cells)} figures match the "
            f"transcription ({100 * ok / len(cells):.0f}%); {sheets_all}/{len(rs)} sheets fully right; of the "
            f"{len(wrong)} disagreements, {flagged} were marked unsure or left blank by the AI.",
            "",
        ]
    blur = [r["blur"] for r in results if "blur" in r]
    if blur:
        careful = sum(1 for b in blur if b["status"] != "read" or b["sure"] < b["figures"] / 2)
        lines += [f"**Blurred sheets ({len(blur)})** (no transcription exists): on {careful} the AI said it could not read "
                  f"the sheet or marked most figures as unsure; figures given {sum(b['figures'] for b in blur)}, of which "
                  f"{sum(b['sure'] for b in blur)} marked sure.", ""]  # fmt: skip
    errors = [r for r in results if "error" in r]
    if errors:
        lines.append(f"Errors: {len(errors)} ({', '.join(r['code'] for r in errors)})")
    lines += [
        "",
        "| Polling unit | Status | Disagreements (field: read vs transcription, AI confidence) |",
        "|---|---|---|",
    ]
    for r in results:
        if "cells" in r:
            bad = "; ".join(f"{c['field']}: {c['read']} vs {c['truth']} ({c['confidence']})"
                            for c in r["cells"] if not c["ok"]) or "none"  # fmt: skip
            lines.append(f"| {r['code']} | {r['status']} | {bad} |")
    text = "\n".join(lines)
    print("\n" + text)
    if s := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(s).open("a").write(text + "\n")


if __name__ == "__main__":
    sys.exit(main())
