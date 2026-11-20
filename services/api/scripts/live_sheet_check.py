"""Live check of the result-sheet reader with the REAL vision model, real Storage and the real database.

Skips (exit 0, clearly reported) when LLM_API_KEY is not set. Otherwise: a fresh organisation and project, the
SYNTHETIC demo file through upload → ingest → validate, then two SYNTHETIC result-sheet photos:
* a sheet that matches the data: the AI must read it accurately and confirming it must raise nothing;
* a sheet with 15 extra votes for one party: confirming the AI's reading must raise the right cross-checks.
Also: the stored photo is a JPEG reachable only through a short-lived link, and non-members cannot see the sheet.
Needs SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, DATABASE_URL, LLM_API_KEY.
"""

from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

if not os.environ.get("LLM_API_KEY", "").strip():
    print("SKIPPED: LLM_API_KEY is not set, so the sheet reader cannot be checked live.")
    if s := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(s).open("a").write("## Live sheet-reader check\n\nSKIPPED — LLM_API_KEY not set.\n")
    sys.exit(0)

import httpx  # noqa: E402
import live_ingest_check as L  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from live_assistant_check import MAPPING, drain  # noqa: E402

from app.assistant.llm import DEFAULT_BASE_URL, list_models  # noqa: E402
from app.main import app  # noqa: E402
from app.sheets.read import choose_vision_model  # noqa: E402
from app.sheets.synthetic import figures_from_csv, photo_like, render  # noqa: E402

COUNTS = ["registered_voters", "accredited_voters", "rejected_votes", "valid_votes", "total_cast"]


def accuracy(read: dict, truth: dict) -> tuple[int, int, list[str]]:
    ok, total, wrong = 0, 0, []
    for k in [*COUNTS, "pu_code"]:
        total += 1
        if str(read.get(k) or "").strip().upper() == str(truth[k]).upper():
            ok += 1
        else:
            wrong.append(f"{k}: read {read.get(k)!r}, true {truth[k]!r}")
    got = {p["party"]: p["votes"] for p in read.get("party_votes", [])}
    for p in truth["party_votes"]:
        total += 1
        if got.get(p["party"]) == p["votes"]:
            ok += 1
        else:
            wrong.append(f"party {p['party']}: read {got.get(p['party'])!r}, true {p['votes']}")
    return ok, total, wrong


def main() -> int:
    check = L.check
    key = os.environ["LLM_API_KEY"].strip()
    listed = list_models(key, DEFAULT_BASE_URL)
    vision = choose_vision_model(key, DEFAULT_BASE_URL)
    print(
        "Vision-capable candidates:",
        [m for m in listed if "llama-4" in m or "vision" in m or "qwen" in m] or "(none)",
        flush=True,
    )
    if not check("a vision model is available", bool(vision) and vision in listed, f"using {vision}"):
        return L.report()

    http = httpx.Client(timeout=120)
    api = TestClient(app, raise_server_exceptions=False)
    a = L.make_user(http, "sheets")
    org = L.rest(http, a, "POST", "rpc/create_organisation", json={"p_name": f"ZZ CI sheet check {L.RUN}"}).json()
    proj = L.rest(http, a, "POST", "projects", extra_headers={"Prefer": "return=representation"},
                  json={"org_id": org, "election_name": "SYNTHETIC sheet check", "election_year": 2027,
                        "country": "Synthetica", "election_level": "other", "created_by": a["id"]}).json()[0]["id"]  # fmt: skip
    auth = {"Authorization": f"Bearer {a['token']}"}
    up = api.post("/uploads", headers={**auth, "Idempotency-Key": f"ci-sheet-{L.RUN}"},
                  json={"project_id": proj, "dataset_name": "SYNTHETIC demo", "original_filename": "SYNTHETIC.csv",
                        "size_bytes": len(L.DEMO), "content_type": "text/csv", "source_type": "synthetic",
                        "source_description": "SYNTHETIC sheet check"}).json()  # fmt: skip
    http.put(up["upload_url"], content=L.DEMO, headers={"Content-Type": "text/csv", "x-upsert": "false"})
    vid = up["dataset_version_id"]
    api.post(f"/dataset-versions/{vid}/finalize", headers=auth)
    drain("si")
    api.put(f"/dataset-versions/{vid}/mapping", headers=auth, json=MAPPING)
    api.post("/jobs", headers=auth, json={"job_type": "validate", "dataset_version_id": vid})
    drain("sv")

    def send(fig: dict, photo: bool = False) -> tuple[int, dict, float]:
        t0 = time.monotonic()
        img = photo_like(render(fig)) if photo else render(fig)
        r = api.post("/sheets", headers=auth, json={"project_id": proj, "dataset_version_id": vid,
                                                    "image_base64": base64.b64encode(img).decode()})  # fmt: skip
        return r.status_code, r.json(), time.monotonic() - t0

    # 1. A sheet that matches the data.
    truth = figures_from_csv(1)
    code, s, took = send(truth)
    check("sheet uploaded and read by the AI", code == 201 and s.get("reading", {}).get("status") == "read",
          f"HTTP {code} in {took:.1f}s; status {s.get('reading', {}).get('status')}; {s.get('message', '')}"
          f"{s.get('reading', {}).get('notes') or ''}")  # fmt: skip
    if code != 201:
        return L.report()
    ok, total, wrong = accuracy(s["reading"]["figures"], truth)
    check("AI reads at least 90% of the figures exactly", ok / total >= 0.9, f"{ok}/{total} exact; {wrong}")
    check("AI marks how sure it is", len(s["reading"]["confidence"]) >= 5, str(s["reading"]["confidence"]))

    img = http.get(s["image_url"]) if s.get("image_url") else None
    check("photo stored as JPEG behind a short-lived link",
          img is not None and img.status_code == 200 and img.content[:2] == b"\xff\xd8" and "token=" in s["image_url"],
          f"HTTP {img.status_code if img else 'no link'}")  # fmt: skip
    anon = http.get(s["image_url"].split("?")[0]) if s.get("image_url") else None
    check("photo is not readable without the link's token", anon is not None and anon.status_code >= 400,
          f"HTTP {anon.status_code if anon else '-'}")  # fmt: skip

    done = api.post(f"/sheets/{s['id']}/confirm", headers=auth, json={"figures": truth}).json()
    check("matching sheet: every check passes and nothing is flagged",
          all(c["ok"] is True for c in done.get("checks", [])) and done.get("finding_ids") == [],
          str([(c["id"], c["ok"]) for c in done.get("checks", [])]))  # fmt: skip

    # 2. A sheet with 15 extra votes for the first party (party scores no longer add up, and differ from data).
    time.sleep(10)
    changed = figures_from_csv(2)
    changed["party_votes"][0]["votes"] += 15
    code, s2, took = send(changed)
    check("second sheet read", code == 201 and s2["reading"]["status"] == "read", f"HTTP {code} in {took:.1f}s")
    if code != 201:
        return L.report()
    done2 = api.post(f"/sheets/{s2['id']}/confirm", headers=auth, json={"figures": s2["reading"]["figures"]}).json()
    failed = {c["id"] for c in done2.get("checks", []) if c["ok"] is False}
    check("changed sheet: party sum and data comparison are flagged",
          {"sheet_party_sum", "sheet_vs_data"} <= failed, f"failed checks {sorted(failed)}")  # fmt: skip
    f = api.get(f"/findings/{done2['finding_ids'][0]}", headers=auth).json() if done2.get("finding_ids") else {}
    check("finding is a cross-check linked to the sheet", f.get("category") == "cross_check"
          and f.get("source_sheet_id") == s2["id"], f"{f.get('category')} {f.get('rule_id')}")  # fmt: skip

    # 3. A rough "phone photo" (tilted, blurred, uneven light, heavy JPEG): report how well the AI copes.
    time.sleep(10)
    rough = figures_from_csv(3)
    code, s3, took = send(rough, photo=True)
    ok3, total3, wrong3 = accuracy(s3.get("reading", {}).get("figures", {}), rough) if code == 201 else (0, 1, [])
    unsure = [k for k, v in s3.get("reading", {}).get("confidence", {}).items() if v != "high"]
    check("rough phone-style photo: AI still reads at least 80% exactly", ok3 / total3 >= 0.8,
          f"HTTP {code} in {took:.1f}s; {ok3}/{total3} exact; status {s3.get('reading', {}).get('status')}; "
          f"note {s3.get('reading', {}).get('notes')!r}; wrong {wrong3}; marked unsure {unsure}")  # fmt: skip

    b = L.make_user(http, "sheets-outsider")
    outsider = api.get(f"/sheets/{s2['id']}", headers={"Authorization": f"Bearer {b['token']}"})
    check("non-members cannot see the sheet", outsider.status_code == 404, f"HTTP {outsider.status_code}")
    return L.report()


if __name__ == "__main__":
    sys.exit(main())
