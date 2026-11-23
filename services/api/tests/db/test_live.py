"""Phase 17: live election-day view and signed submission packs, on the migrated test DB."""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import uuid
import zipfile

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row

from app.packs import signing
from app.sheets.synthetic import figures_from_csv, render

from .conftest import as_service, as_user
from .test_field import ARRIVAL, send
from .test_hierarchy import assign, member
from .test_ingestion import H
from .test_sheets import ai_reply, b64, vision  # noqa: F401

PU = "LV-06-01"
KEY = base64.b64encode(bytes(range(32))).decode()


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    monkeypatch.setenv("SUBMISSION_SIGNING_KEY", KEY)


@pytest.fixture(scope="module")
def unit(world):
    agent = member(world, "field_agent")
    assign(world, agent, "polling_unit", "S6", "L6", "W6", PU)
    return agent


def area_row(summary, lga="L6"):
    return next(a for a in summary["areas"] if a["lga"] == lga)


def test_live_view_counts_what_was_reported(api, world, unit):
    p = world.a.rows["projects"]
    assert send(api, world, unit, "arrival", ARRIVAL, pu=PU).status_code == 201
    inc = send(api, world, unit, "incident", {"category": "vote_buying", "severity": "critical",
               "description": "Cash handed out near the queue.", "ongoing": True}, pu=PU)  # fmt: skip
    assert inc.status_code == 201
    r = api.get(f"/projects/{p}/live", headers=H(world.a.users["viewer"]))
    assert r.status_code == 200, r.text
    s = r.json()
    a = area_row(s)
    assert (a["state"], a["assigned"], a["reporting"], a["incidents"], a["critical"]) == ("S6", 1, 1, 1, 1)
    assert s["coverage"]["opened"] >= 1 and s["incidents"]["critical"] >= 1
    mine = next(x for x in s["incidents"]["recent"] if x["id"] == inc.json()["id"])
    assert mine["label"] == "Vote buying" and mine["escalation_id"] and mine["escalation_status"] == "open"
    assert any(c["category"] == "vote_buying" for c in s["incidents"]["by_category"])
    # Field agents report; they do not get the whole picture. Other organisations get nothing.
    assert api.get(f"/projects/{p}/live", headers=H(unit)).status_code == 403
    assert api.get(f"/projects/{p}/live", headers=H(world.b.users["admin"])).status_code == 404


def test_results_roll_up_from_confirmed_sheets(api, world, unit, vision):  # noqa: F811
    p = world.a.rows["projects"]
    before = api.get(f"/projects/{p}/live", headers=H(world.a.users["admin"])).json()["results"]
    fig = figures_from_csv(3)
    vision(ai_reply(fig))
    up = api.post(
        "/sheets", json={"project_id": p, "image_base64": b64(render(fig))}, headers=H(unit, str(uuid.uuid4()))
    )
    assert up.status_code == 201, up.text
    sid = up.json()["id"]
    figures = {**{k: v["value"] for k, v in ai_reply(fig).items() if isinstance(v, dict)},
               "party_votes": fig["party_votes"], "pu_code": PU, "state": "S6", "lga": "L6", "ward": "W6"}  # fmt: skip
    ok = api.post(f"/sheets/{sid}/confirm", json={"figures": figures}, headers=H(unit, str(uuid.uuid4())))
    assert ok.status_code == 200, ok.text
    assert send(api, world, unit, "result", {"sheet_id": sid}, pu=PU).status_code == 201
    s = api.get(f"/projects/{p}/live", headers=H(world.a.users["admin"])).json()
    assert area_row(s)["results"] == 1
    assert s["results"]["units"] == before["units"] + 1
    assert s["results"]["valid_votes"] == before["valid_votes"] + (figures["valid_votes"] or 0)
    top = fig["party_votes"][0]
    was = {x["party"]: x["votes"] for x in before["party_totals"]}.get(top["party"].upper(), 0)
    assert {x["party"]: x["votes"] for x in s["results"]["party_totals"]}[top["party"].upper()] == was + top["votes"]


def _zip(api, world, pack_id):
    d = api.get(f"/submission-packs/{pack_id}/download", headers=H(world.a.users["viewer"]))
    assert d.status_code == 200, d.text
    return base64.b64decode(d.json()["zip_base64"]), d.json()


def _rezip(data: bytes, change: dict[str, bytes]) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in src.namelist():
            z.writestr(n, change.get(n, src.read(n)))
        for n, v in change.items():
            if n not in src.namelist():
                z.writestr(n, v)
    return buf.getvalue()


def test_signed_pack_is_made_recorded_and_checked(api, world, unit):
    p = world.a.rows["projects"]
    assert api.post(f"/projects/{p}/submission-packs", headers=H(world.a.users["analyst"], "k-" + uuid.uuid4().hex)
                    ).status_code == 403  # fmt: skip
    key = "k-" + uuid.uuid4().hex
    r = api.post(f"/projects/{p}/submission-packs", headers=H(world.a.users["admin"], key))
    assert r.status_code == 201, r.text
    pack = r.json()
    assert pack["counts"]["critical_incidents"] >= 1 and pack["counts"]["polling_units_with_results"] >= 1
    again = api.post(f"/projects/{p}/submission-packs", headers=H(world.a.users["admin"], key))
    assert again.json()["id"] == pack["id"]  # same Idempotency-Key, no second pack
    listed = api.get(f"/projects/{p}/submission-packs", headers=H(world.a.users["viewer"])).json()["items"]
    assert listed[0]["id"] == pack["id"]
    assert api.get(f"/submission-packs/{pack['id']}/download", headers=H(unit)).status_code == 403
    assert api.get(f"/submission-packs/{pack['id']}/download", headers=H(world.b.users["admin"])).status_code == 404

    data, meta = _zip(api, world, pack["id"])
    assert meta["filename"].endswith(".zip")
    z = zipfile.ZipFile(io.BytesIO(data))
    assert {"manifest.json", "manifest.sig", "public_key.txt", "VERIFY.txt", "results_by_polling_unit.csv",
            "field_reports.csv", "incidents.csv", "escalations.csv", "summary.json"} == set(z.namelist())  # fmt: skip
    m = json.loads(z.read("manifest.json"))
    assert m["key_id"] == pack["key_id"] and "not an INEC document" in m["notice"]
    rows = list(csv.DictReader(io.StringIO(z.read("field_reports.csv").decode())))
    assert rows and all(r["agent_ref"].startswith("agent-") for r in rows)
    assert b"@example.test" not in data  # observers are not named in the pack
    res = list(csv.DictReader(io.StringIO(z.read("results_by_polling_unit.csv").decode())))
    assert any(r["pu_code"] == PU and r["source"] == "field report" for r in res)

    # Anyone can get the public key; it matches the pack.
    pub = api.get("/signing-key").json()
    assert pub["public_key"] == z.read("public_key.txt").decode() and pub["key_id"] == pack["key_id"]

    def verify(blob: bytes, who="viewer"):
        out = api.post("/submission-packs/verify", json={"zip_base64": base64.b64encode(blob).decode()},
                       headers=H(world.a.users[who]))  # fmt: skip
        assert out.status_code == 200, out.text
        return out.json(), {c["id"]: c["ok"] for c in out.json()["checks"]}

    good, checks = verify(data)
    assert good["valid"] and all(checks.values()) and good["pack_id"] == pack["id"], good
    assert verify(data, "analyst")[0]["valid"]

    tampered = _rezip(data, {"results_by_polling_unit.csv": z.read("results_by_polling_unit.csv") + b"X,1\n"})
    bad, checks = verify(tampered)
    assert not bad["valid"] and checks["files"] is False and checks["signature"] is True

    edited = m | {"counts": {**m["counts"], "incidents": 0}}
    forged = _rezip(data, {"manifest.json": json.dumps(edited, indent=2).encode()})
    bad, checks = verify(forged)
    assert not bad["valid"] and checks["signature"] is False and checks["issued"] is False

    # Re-signed with someone else's key: the signature checks out, but it is not this platform's key.
    os.environ["SUBMISSION_SIGNING_KEY"] = base64.b64encode(b"\x07" * 32).decode()
    stranger = signing.load()
    os.environ["SUBMISSION_SIGNING_KEY"] = KEY
    raw = json.dumps(edited, indent=2).encode()
    resigned = _rezip(data, {"manifest.json": raw, "manifest.sig": signing.sign(stranger, raw).encode(),
                             "public_key.txt": stranger.public_b64.encode()})  # fmt: skip
    bad, checks = verify(resigned)
    assert not bad["valid"] and checks["signature"] is True and checks["key"] is False

    added = _rezip(data, {"extra.csv": b"a\n1\n"})
    assert verify(added)[1]["extra"] is False
    junk, checks = verify(b"not a zip")
    assert not junk["valid"] and checks == {"readable": False, "issued": False}

    with psycopg.connect(world.dsn, row_factory=dict_row, autocommit=True) as c:
        with pytest.raises(errors.Error), as_service(c, world.a.users["admin"]) as s:
            s.execute("UPDATE public.submission_packs SET counts = '{}' WHERE id = %s", (pack["id"],))
        with as_user(c, unit) as u:
            assert u.execute("SELECT count(*) AS n FROM public.submission_packs").fetchone()["n"] == 0
        with as_user(c, world.a.users["viewer"]) as u:
            assert u.execute("SELECT count(*) AS n FROM public.submission_packs").fetchone()["n"] >= 1


def test_no_signing_key_means_no_pack(api, world, monkeypatch):
    monkeypatch.delenv("SUBMISSION_SIGNING_KEY")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    r = api.post(
        f"/projects/{world.a.rows['projects']}/submission-packs", headers=H(world.a.users["admin"], "k-nokey-1")
    )
    assert r.status_code == 503
    assert api.get("/signing-key").status_code == 503
