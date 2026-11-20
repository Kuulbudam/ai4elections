"""Result-sheet reader end to end on the migrated test DB, with a stand-in vision model.

Upload → AI reading stored as produced → person confirms (with a correction) → arithmetic and data comparison →
cross_check findings in the review queue. Permissions, once-only confirmation and immutability are enforced.
"""

from __future__ import annotations

import base64
import json
import uuid

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row

from app.main import app
from app.routes import sheets as sheets_route
from app.sheets.synthetic import figures_from_csv, render

from .conftest import as_user
from .test_analysis_job import _validate
from .test_ingestion import H
from .test_validation_job import ingested  # noqa: F401


class FakeVision:
    name = "fake-vision"

    def __init__(self, reply: dict | str):
        self.reply = reply
        self.calls = 0

    def read_image(self, prompt, jpeg):
        self.calls += 1
        assert jpeg[:2] == b"\xff\xd8", "the model gets the cleaned JPEG"
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def ai_reply(fig: dict) -> dict:
    out = {"legible": True, "notes": None, "party_votes": [
        {"party": p["party"], "votes": p["votes"], "confidence": "high"} for p in fig["party_votes"]]}  # fmt: skip
    for k in ("pu_code", "state", "lga", "ward", "polling_unit_name", "registered_voters", "accredited_voters",
              "rejected_votes", "valid_votes", "total_cast"):  # fmt: skip
        out[k] = {"value": fig[k], "confidence": "medium" if k == "valid_votes" else "high"}
    return out


@pytest.fixture()
def vision():
    holder = {}

    def use(reply):
        holder["m"] = FakeVision(reply)
        app.dependency_overrides[sheets_route.get_vision] = lambda: holder["m"]
        return holder["m"]

    yield use
    app.dependency_overrides.pop(sheets_route.get_vision, None)


@pytest.fixture()
def validated(api, world, ingested):  # noqa: F811
    _validate(api, world, ingested)
    return ingested


def b64(png: bytes) -> str:
    return base64.b64encode(png).decode()


def upload(api, world, vid, who="researcher", fig=None, key=None):
    body = {"project_id": world.a.rows["projects"], "dataset_version_id": vid,
            "image_base64": b64(render(fig or figures_from_csv(1)))}  # fmt: skip
    return api.post("/sheets", json=body, headers=H(world.a.users[who], key or str(uuid.uuid4())))


def q(world, sql, *args):
    with psycopg.connect(world.dsn, row_factory=dict_row) as c:
        return c.execute(sql, args).fetchall()


def test_upload_read_confirm_check_and_findings(api, world, validated, vision):
    fig = figures_from_csv(1)
    sheet_fig = json.loads(json.dumps(fig))
    sheet_fig["party_votes"][0]["votes"] += 15  # the sheet shows 15 more votes for PA than the data
    model = vision(ai_reply(sheet_fig | {"valid_votes": fig["valid_votes"] + 1}))  # AI misreads valid votes by 1
    key = str(uuid.uuid4())
    r = upload(api, world, validated, fig=sheet_fig, key=key)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["status"] == "read" and s["model"] == "fake-vision" and model.calls == 1
    assert s["reading"]["figures"]["valid_votes"] == fig["valid_votes"] + 1
    assert s["reading"]["confidence"]["valid_votes"] == "medium" and s["checks"] is None and s["finding_ids"] == []
    assert s["image_url"].startswith("https://storage.test/read/sheets/")
    path = f"{world.a.id}/{world.a.rows['projects']}/{s['id']}.jpg"
    assert api.storage.objects[f"sheets:{path}"][:2] == b"\xff\xd8"

    again = upload(api, world, validated, fig=sheet_fig, key=key)
    assert again.status_code == 201 and again.json()["id"] == s["id"] and model.calls == 1, "idempotent replay"

    # The person corrects the AI's misread valid votes; the PA difference is real on the sheet.
    figures = dict(s["reading"]["figures"], valid_votes=fig["valid_votes"] + 15, total_cast=fig["total_cast"] + 15)
    c = api.post(f"/sheets/{s['id']}/confirm", json={"figures": figures},
                 headers=H(world.a.users["analyst"], str(uuid.uuid4())))  # fmt: skip
    assert c.status_code == 200, c.text
    done = c.json()
    checks = {x["id"]: x for x in done["checks"]}
    assert checks["sheet_party_sum"]["ok"] is True and checks["sheet_total"]["ok"] is True
    assert checks["sheet_vs_data"]["ok"] is False and "PA votes" in checks["sheet_vs_data"]["detail"]
    assert done["comparison"]["matched"] and done["comparison"]["source_row_number"] == 2
    assert checks["sheet_over_accredited"]["ok"] is False, "15 extra votes push votes cast above accredited voters"
    assert len(done["finding_ids"]) == 2 and done["status"] == "confirmed"
    found = {x["rule_id"]: x["id"] for x in q(world, "SELECT rule_id, id::text FROM public.findings WHERE id = ANY(%s::uuid[])",
                                              done["finding_ids"])}  # fmt: skip
    assert set(found) == {"sheet_vs_data", "sheet_over_accredited"}

    f = api.get(f"/findings/{found['sheet_vs_data']}", headers=H(world.a.users["viewer"])).json()
    assert f["category"] == "cross_check" and f["rule_id"] == "sheet_vs_data" and f["source_sheet_id"] == s["id"]
    assert f["pu_code"] == fig["pu_code"] and f["review_status"] == "unreviewed"
    assert f["evidence"][0]["source_row_number"] == 2 and "confirmed by a person" in f["explanation"]
    listed = api.get("/findings", params={"project_id": world.a.rows["projects"], "category": "cross_check"},
                     headers=H(world.a.users["viewer"])).json()  # fmt: skip
    assert set(done["finding_ids"]) <= {x["id"] for x in listed["items"]}

    # Reviews work on sheet findings like any other.
    rv = api.post(f"/findings/{f['id']}/reviews", json={"decision": "needs_more_evidence",
                  "reason": "Ask the observer for a second photo of the sheet."},
                  headers=H(world.a.users["analyst"], str(uuid.uuid4())))  # fmt: skip
    assert rv.status_code == 201, rv.text

    twice = api.post(f"/sheets/{s['id']}/confirm", json={"figures": figures},
                     headers=H(world.a.users["analyst"], str(uuid.uuid4())))  # fmt: skip
    assert twice.status_code == 409

    lst = api.get("/sheets", params={"project_id": world.a.rows["projects"]}, headers=H(world.a.users["viewer"]))
    assert lst.status_code == 200 and s["id"] in [x["id"] for x in lst.json()["items"]]
    assert all(x["image_url"] is None for x in lst.json()["items"]), "links only on the single-sheet view"
    one = api.get(f"/sheets/{s['id']}", headers=H(world.a.users["viewer"])).json()
    assert one["image_url"] and one["confirmed"]["valid_votes"] == fig["valid_votes"] + 15

    ev = q(world, "SELECT action FROM public.audit_events WHERE entity_id = %s ORDER BY seq", s["id"])
    assert [e["action"] for e in ev] == ["result_sheets.insert", "result_sheets.update"]


def test_arithmetic_failure_flags_even_when_data_matches(api, world, validated, vision):
    fig = figures_from_csv(2)
    vision(ai_reply(fig))
    s = upload(api, world, validated, fig=fig).json()
    figures = dict(s["reading"]["figures"], total_cast=fig["total_cast"] + 40, accredited_voters=fig["total_cast"])
    done = api.post(f"/sheets/{s['id']}/confirm", json={"figures": figures},
                    headers=H(world.a.users["researcher"], str(uuid.uuid4()))).json()  # fmt: skip
    checks = {x["id"]: x for x in done["checks"]}
    assert checks["sheet_total"]["ok"] is False and checks["sheet_over_accredited"]["ok"] is False
    assert checks["sheet_vs_data"]["ok"] is False  # total and accredited now differ from the data too
    rules = {r["rule_id"] for r in q(world, "SELECT rule_id FROM public.findings WHERE id = ANY(%s::uuid[])",
                                      done["finding_ids"])}  # fmt: skip
    assert rules == {"sheet_total", "sheet_over_accredited", "sheet_vs_data"}


def test_unmatched_code_and_no_model_typing(api, world, validated, vision):
    app.dependency_overrides[sheets_route.get_vision] = lambda: None
    s = upload(api, world, validated).json()
    assert s["reading"]["status"] == "no_model" and s["model"] is None
    figures = dict(figures_from_csv(3), pu_code="ZZ-99-99")
    done = api.post(f"/sheets/{s['id']}/confirm", json={"figures": figures},
                    headers=H(world.a.users["admin"], str(uuid.uuid4()))).json()  # fmt: skip
    checks = {x["id"]: x for x in done["checks"]}
    assert checks["sheet_vs_data"]["ok"] is None and done["comparison"]["matched"] is False
    assert done["finding_ids"] == [], "sums are right and nothing to compare: no findings"


def test_codes_match_however_they_are_written(api, world, validated, vision):
    fig = figures_from_csv(4)
    vision(ai_reply(fig))
    s = upload(api, world, validated, fig=fig).json()
    figures = dict(s["reading"]["figures"], pu_code=" " + fig["pu_code"].replace("-", " / ").lower())
    done = api.post(f"/sheets/{s['id']}/confirm", json={"figures": figures},
                    headers=H(world.a.users["analyst"], str(uuid.uuid4()))).json()  # fmt: skip
    assert done["comparison"]["matched"] is True and {x["ok"] for x in done["checks"]} == {True}


def test_permissions_and_bad_input(api, world, validated, vision):
    vision(ai_reply(figures_from_csv(1)))
    assert upload(api, world, validated, who="viewer").status_code == 403
    body = {"project_id": world.b.rows["projects"], "dataset_version_id": validated, "image_base64": "A" * 200}
    assert api.post("/sheets", json=body, headers=H(world.b.users["admin"])).status_code == 404
    bad = {"project_id": world.a.rows["projects"], "dataset_version_id": validated,
           "image_base64": base64.b64encode(b"%PDF" * 100).decode()}  # fmt: skip
    r = api.post("/sheets", json=bad, headers=H(world.a.users["analyst"]))
    assert r.status_code == 400 and "photo" in r.json()["message"]
    unvalidated = dict(bad, dataset_version_id=world.a.rows["dataset_versions"])
    assert api.post("/sheets", json=unvalidated, headers=H(world.a.users["analyst"])).status_code == 409
    s = upload(api, world, validated).json()
    dup = dict(s["reading"]["figures"], party_votes=[{"party": "PA", "votes": 1}, {"party": "pa", "votes": 2}])
    r = api.post(f"/sheets/{s['id']}/confirm", json={"figures": dup}, headers=H(world.a.users["analyst"]))
    assert r.status_code == 400 and "twice" in r.json()["message"]
    assert api.post(f"/sheets/{s['id']}/confirm", json={"figures": s["reading"]["figures"]},
                    headers=H(world.a.users["viewer"])).status_code == 403  # fmt: skip
    assert api.get(f"/sheets/{s['id']}", headers=H(world.b.users["admin"])).status_code == 404
    assert api.get("/sheets", params={"project_id": world.a.rows["projects"]},
                   headers=H(world.b.users["admin"])).status_code == 404  # fmt: skip


def test_sheets_are_immutable_after_confirmation(api, world, validated, vision, conn):
    vision(ai_reply(figures_from_csv(5)))
    s = upload(api, world, validated).json()
    api.post(f"/sheets/{s['id']}/confirm", json={"figures": s["reading"]["figures"]},
             headers=H(world.a.users["analyst"], str(uuid.uuid4())))  # fmt: skip
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute("UPDATE public.result_sheets SET checks = '[]' WHERE id = %s", (s["id"],))
    conn.rollback()
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute("DELETE FROM public.result_sheets WHERE id = %s", (s["id"],))
    conn.rollback()
    with as_user(conn, world.a.users["admin"]) as c, pytest.raises(errors.InsufficientPrivilege):
        c.execute("INSERT INTO public.result_sheets (org_id) VALUES (%s)", (world.a.id,))


def test_sheet_findings_appear_in_the_run_queue_and_in_reports(api, world, ingested, vision):  # noqa: F811
    from .test_analysis_job import _analyse
    from .test_ingestion import drain
    from .test_reports import make

    _validate(api, world, ingested)
    assert _analyse(api, world, ingested).status_code == 202
    drain(world)
    run = q(world, "SELECT latest_analysis_run_id::text AS r FROM public.dataset_versions WHERE id=%s", ingested)[0][
        "r"
    ]
    fig = figures_from_csv(6)
    vision(ai_reply(fig))
    s = upload(api, world, ingested, fig=fig).json()
    figures = dict(s["reading"]["figures"], valid_votes=fig["valid_votes"] + 1)  # party scores no longer add up
    done = api.post(f"/sheets/{s['id']}/confirm", json={"figures": figures},
                    headers=H(world.a.users["analyst"], str(uuid.uuid4()))).json()  # fmt: skip
    in_run = api.get("/findings", params={"project_id": world.a.rows["projects"], "analytical_run_id": run,
                                          "category": "cross_check"}, headers=H(world.a.users["viewer"])).json()  # fmt: skip
    assert set(done["finding_ids"]) <= {x["id"] for x in in_run["items"]}
    rep = make(api, world, run).json()
    md = api.get(f"/reports/{rep['id']}/export", params={"format": "md"}, headers=H(world.a.users["analyst"])).text
    assert "## Cross-checks: result sheets and collation" in md and fig["pu_code"] in md
    assert "party scores do not add up" in md
