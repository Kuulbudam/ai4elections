"""Phase 7: report snapshots, reproducible hashes, safe exports, no AI."""

from __future__ import annotations

import ast
import csv
import io
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

from app.reports import build

from .test_analysis_job import _analyse, _validate
from .test_ingestion import H, drain
from .test_validation_job import ingested  # noqa: F401


@pytest.fixture()
def run_id(api, world, ingested):  # noqa: F811
    _validate(api, world, ingested)
    assert _analyse(api, world, ingested).status_code == 202
    drain(world)
    with psycopg.connect(world.dsn) as c:
        return str(c.execute("SELECT latest_analysis_run_id FROM public.dataset_versions WHERE id=%s",
                             (ingested,)).fetchone()[0])  # fmt: skip


def make(api, world, run, who="analyst", title=None):
    body = {"project_id": world.a.rows["projects"], "analytical_run_id": run, **({"title": title} if title else {})}
    return api.post("/reports", json=body, headers=H(world.a.users[who], str(uuid.uuid4())))


def test_same_inputs_same_hash_and_reviews_change_it(api, world, run_id):
    a = make(api, world, run_id).json()
    b = make(api, world, run_id, who="admin").json()  # another person, later, after an audit event was added
    assert a["id"] != b["id"] and a["content_hash"] == b["content_hash"]
    assert a["audit_checkpoint"]["audit_event_id"] != b["audit_checkpoint"]["audit_event_id"]
    with psycopg.connect(world.dsn) as c:
        fid = c.execute("SELECT id::text FROM public.findings WHERE analytical_run_id=%s ORDER BY id LIMIT 1",
                        (run_id,)).fetchone()[0]  # fmt: skip
    api.post(f"/findings/{fid}/reviews", json={"decision": "acknowledged", "reason": "Compared with the sheet."},
             headers=H(world.a.users["analyst"]))  # fmt: skip
    c2 = make(api, world, run_id).json()
    assert c2["content_hash"] != a["content_hash"], "a new review decision is new input"
    # the stored hash is exactly SHA-256 of the canonical stored body
    with psycopg.connect(world.dsn) as c:
        body, h = c.execute("SELECT body, content_hash FROM public.reports WHERE id=%s", (c2["id"],)).fetchone()
    assert build.content_hash(body) == h


def test_report_contents(api, world, run_id):
    r = make(api, world, run_id).json()
    with psycopg.connect(world.dsn) as c:
        body, cp_id, cp_hash, org = c.execute(
            "SELECT body, audit_event_id::text, audit_event_hash, org_id::text FROM public.reports WHERE id=%s",
            (r["id"],),
        ).fetchone()
        ev = c.execute("SELECT event_hash FROM public.audit_events WHERE id=%s", (cp_id,)).fetchone()
        ds = c.execute(
            "SELECT sha256 FROM public.dataset_versions WHERE latest_analysis_run_id=%s", (run_id,)
        ).fetchone()
    assert ev[0] == cp_hash, "checkpoint is a real event in the org's chain"
    assert body["dataset"]["sha256"] == ds[0]
    assert body["analysis"]["engine_version"] and body["analysis"]["parameters"]["threshold"] == 3.5
    assert len(body["findings"]) == 140 + 105 and body["validation"]["rows"] == 12_023
    assert "not evidence of fraud" in body["limitations"]
    assert all(f["evidence"]["source_row_number"] for f in body["findings"])
    ids = [f["finding_id"] for f in body["findings"]]
    assert ids == sorted(ids), "findings in a defined order (ADR-0005)"
    assert {k for k in body} >= {"project", "dataset", "analysis", "methodology", "validation", "findings", "claims",
                                 "limitations"}  # fmt: skip
    assert "generated_at" not in body and "id" not in body


def test_claims_are_labelled(api, world, run_id):
    with psycopg.connect(world.dsn) as c:
        fid = c.execute(
            "SELECT id::text FROM public.findings WHERE analytical_run_id=%s LIMIT 1", (run_id,)
        ).fetchone()[0]
    api.post("/claims", json={"project_id": world.a.rows["projects"], "finding_id": fid,
                              "statement": "An observer says counting started at night."},
             headers=H(world.a.users["researcher"]))  # fmt: skip
    r = make(api, world, run_id).json()
    md = api.get(f"/reports/{r['id']}/export", params={"format": "md"}, headers=H(world.a.users["analyst"])).text
    assert "**UNVERIFIED ALLEGATION**: An observer says counting started at night." in md


def test_reports_are_immutable(api, world, run_id):
    rid = make(api, world, run_id).json()["id"]
    for sql in ("UPDATE public.reports SET title='x' WHERE id=%s", "DELETE FROM public.reports WHERE id=%s"):
        with psycopg.connect(world.dsn) as c, pytest.raises(errors.InsufficientPrivilege):
            c.execute(sql, (rid,))


@pytest.mark.parametrize("who,status", [("admin", 201), ("analyst", 201), ("researcher", 403), ("viewer", 403)])
def test_generate_permissions(api, world, run_id, who, status):
    assert make(api, world, run_id, who=who).status_code == status


def test_export_permissions_and_other_org(api, world, run_id):
    rid = make(api, world, run_id).json()["id"]
    for who, code in (("researcher", 403), ("viewer", 403)):
        assert (
            api.get(f"/reports/{rid}/export", params={"format": "csv"}, headers=H(world.a.users[who])).status_code
            == code
        )
    assert (
        api.get(f"/reports/{rid}/export", params={"format": "csv"}, headers=H(world.b.users["admin"])).status_code
        == 404
    )
    other_run = {"project_id": world.b.rows["projects"], "analytical_run_id": run_id}
    assert api.post("/reports", json=other_run, headers=H(world.b.users["admin"])).status_code == 404


def test_csv_export_is_formula_safe_and_numbers_are_numbers(api, world, run_id):
    rid = make(api, world, run_id, title='=HYPERLINK("http://evil")').json()["id"]
    r = api.get(f"/reports/{rid}/export", params={"format": "csv"}, headers=H(world.a.users["analyst"]))
    assert r.status_code == 200 and r.headers["x-report-content-hash"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["# report", '\'=HYPERLINK("http://evil")']
    head = rows.index(build.CSV_COLUMNS)
    data = rows[head + 1 :]
    assert len(data) == 140 + 105
    zcol = build.CSV_COLUMNS.index("robust_z")
    negs = [d[zcol] for d in data if d[zcol].startswith("-")]
    assert negs and all(float(x) < 0 for x in negs), "negative numbers stay numeric, not quoted text"
    for d in data:
        for cell in d:
            assert not cell.startswith(("=", "+", "@", "\t", "\r")) or cell.startswith("'")


@pytest.mark.parametrize(
    "value,expected",
    [("=1+1", "'=1+1"), ("+44", "'+44"), ("-cmd", "'-cmd"), ("@SUM(A1)", "'@SUM(A1)"), ("\tx", "'\tx"), ("\rx", "'\rx"),
     ("safe", "safe"), (-4.25, -4.25), (12, 12), (None, "")],
)  # fmt: skip
def test_safe_text(value, expected):
    assert build.safe_text(value) == expected


def test_markdown_export_escapes_and_is_reproducible(api, world, run_id):
    rid = make(api, world, run_id, title="<script>x</script> | =cmd").json()["id"]
    md = api.get(f"/reports/{rid}/export", params={"format": "md"}, headers=H(world.a.users["analyst"])).text
    assert "<script>" not in md and "&lt;script&gt;" in md
    assert "Statistical anomalies are not evidence of fraud" in md
    assert md.count("| ") > 100


def test_reports_use_no_ai():
    src = (Path(build.__file__)).read_text() + (Path(build.__file__).parents[1] / "routes" / "reports.py").read_text()
    imported = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m.startswith("app.assistant") for m in imported)
    assert "llm" not in src.lower().replace("allow_nan", "")
