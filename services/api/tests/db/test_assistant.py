"""POST /assistant/ask on the migrated test DB, with a scripted stand-in model.

The scripted model makes the same kind of tool calls a real model would, and its final answer is
built from what the lookups actually returned, so these tests exercise the real lookups, the
project binding, permissions, storage of every question, and the safety checks.
"""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from psycopg import errors

from app.main import app
from app.routes import assistant as assistant_route

from .conftest import as_user
from .test_analysis_job import _analyse, _validate
from .test_ingestion import H, drain
from .test_validation_job import ingested  # noqa: F401


class Scripted:
    name = "scripted-test-model"

    def __init__(self, steps):
        self.steps = list(steps)
        self.tool_results: list[dict] = []

    def chat(self, messages, tool_specs, temperature=0):
        self.tool_results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        step = self.steps.pop(0)
        return step(self) if callable(step) else step


def call(name, args):
    return {"content": None, "tool_calls": [{"id": name, "type": "function",
                                            "function": {"name": name, "arguments": json.dumps(args)}}]}  # fmt: skip


@pytest.fixture()
def model():
    holder = {}

    def use(steps):
        holder["m"] = Scripted(steps)
        app.dependency_overrides[assistant_route.get_model] = lambda: holder["m"]
        return holder["m"]

    yield use
    app.dependency_overrides.pop(assistant_route.get_model, None)


def ask(api, world, who, question="Which areas have the most unusual turnout?", org="a", project=None):
    o = getattr(world, org)
    body = {"project_id": project or world.a.rows["projects"], "question": question}
    return api.post("/assistant/ask", json=body, headers=H(o.users[who], str(uuid.uuid4())))


@pytest.fixture()
def analysed(api, world, ingested):  # noqa: F811
    _validate(api, world, ingested)
    assert _analyse(api, world, ingested).status_code == 202
    drain(world)
    return ingested


def test_answer_built_from_real_lookups_is_grounded_cited_and_stored(api, world, analysed, model):
    vid = analysed

    def answer(m):
        overview, areas, found, detail = m.tool_results[-4:]
        top = max(areas["areas"], key=lambda a: a["findings"]["unusual_turnout"])
        f = found["findings"][0]
        return {"content": (
            f"{top['state']} has the most unusual-turnout flags ({top['findings']['unusual_turnout']}); its turnout is "
            f"{top['turnout_percent']}% across {top['turnout_units_counted']} counted units. "
            f"The strongest is {f['pu_code']}: {f['value_percent']}% against a ward median of {f['peer_median_percent']}%. "
            f"Source row {detail['source_row']['row_number']}. {found['matching']} very-high turnout flags in total."
        )}  # fmt: skip

    m = model([
        call("project_overview", {}),
        lambda m: call("area_totals", {"dataset_version_id": vid, "level": "state"}),
        call("search_findings", {"indicator": "turnout_rate", "severity": "very_high", "limit": 5,
                                 "dataset_version_id": vid}),  # fmt: skip
        lambda m: call("get_finding", {"finding_id": m.tool_results[-1]["findings"][0]["finding_id"]}),
        answer,
    ])  # fmt: skip
    r = ask(api, world, "analyst")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["grounded"] is True and out["note"] is None, out["answer"]
    assert [x["name"] for x in out["lookups"]] == ["project_overview", "area_totals", "search_findings", "get_finding"]
    assert all(x["ok"] for x in out["lookups"])
    assert m.tool_results[2]["matching"] == 15  # the 15 planted very-high turnout outliers
    kinds = {c["kind"] for c in out["citations"]}
    assert {"version", "areas", "finding"} <= kinds
    assert any(c["href"].startswith("/findings/") for c in out["citations"])
    with psycopg.connect(world.dsn) as c:
        row = c.execute("SELECT question, grounded, model, tools FROM public.assistant_queries WHERE id=%s",
                        (out["id"],)).fetchone()  # fmt: skip
        ev = c.execute(
            "SELECT action, actor_id::text FROM public.audit_events WHERE entity_id=%s", (out["id"],)
        ).fetchone()
    assert row[1] is True and row[2] == "scripted-test-model" and len(row[3]) == 4
    assert ev == ("assistant.ask", world.a.users["analyst"])


def test_lookups_cannot_reach_another_project_or_org(api, world, analysed, model):
    other_finding = world.b.rows["findings"]
    other_version = world.b.rows["dataset_versions"]
    m = model([
        call("get_finding", {"finding_id": other_finding}),
        call("area_totals", {"dataset_version_id": other_version, "level": "state"}),
        call("search_findings", {"dataset_version_id": other_version}),
        call("validation_issues", {"dataset_version_id": "'; DROP TABLE findings; --"}),
        {"content": "I could not find that."},
    ])  # fmt: skip
    r = ask(api, world, "admin", "Show me finding X")
    assert r.status_code == 200
    assert [x["ok"] for x in r.json()["lookups"]] == [False, False, False, False]
    assert all(set(t) == {"error"} for t in m.tool_results), m.tool_results  # nothing but an error came back


@pytest.mark.parametrize("who,status", [("admin", 200), ("analyst", 200), ("researcher", 403), ("viewer", 403)])
def test_only_admins_and_analysts_may_ask(api, world, model, who, status):
    model([{"content": "Hello."}])
    assert ask(api, world, who, "hello").status_code == status


def test_other_org_gets_404_and_no_model_gets_503(api, world, model):
    model([{"content": "x"}])
    assert ask(api, world, "admin", org="b").status_code == 404
    app.dependency_overrides[assistant_route.get_model] = lambda: None
    r = ask(api, world, "admin")
    assert r.status_code == 503 and "not set up" in r.json()["message"]
    s = api.get("/assistant/status", headers=H(world.a.users["viewer"])).json()
    assert s == {"enabled": False, "model": None}


def test_wording_guard_and_log_is_append_only(api, world, analysed, model):
    model([call("project_overview", {}), {"content": "Turnout here shows clear electoral fraud."}])
    out = ask(api, world, "analyst", "Was there fraud?").json()
    assert "fraud or rigging" in out["answer"] and out["note"] == "wording"
    with psycopg.connect(world.dsn) as c, pytest.raises(errors.InsufficientPrivilege):
        c.execute("UPDATE public.assistant_queries SET answer='edited' WHERE id=%s", (out["id"],))


def test_log_visible_to_admins_and_analysts_only(api, world, analysed, model, conn):
    model([{"content": "Hello."}])
    qid = ask(api, world, "analyst", "hello").json()["id"]
    for who, n in (("admin", 1), ("analyst", 1), ("researcher", 0), ("viewer", 0)):
        with as_user(conn, world.a.users[who]) as c:
            got = c.execute("SELECT count(*) AS n FROM public.assistant_queries WHERE id=%s", (qid,)).fetchone()["n"]
        assert got == n, who
    with as_user(conn, world.b.users["admin"]) as c:
        assert c.execute("SELECT count(*) AS n FROM public.assistant_queries WHERE id=%s", (qid,)).fetchone()["n"] == 0
