"""Phase 19: AI summary at the end of Findings, written only from counts the server makes."""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from psycopg import errors

from app.main import app
from app.routes import summary as summary_route

from .test_assistant import Scripted, analysed  # noqa: F401
from .test_hierarchy import member
from .test_ingestion import H
from .test_validation_job import ingested  # noqa: F401


def write(api, world, who="analyst", language="en", org="a"):
    o = getattr(world, org)
    return api.post(f"/projects/{world.a.rows['projects']}/findings-summary", json={"language": language},
                    headers=H(o.users[who], str(uuid.uuid4())))  # fmt: skip


def from_facts(messages):
    facts = json.loads(messages[1]["content"].removeprefix("Facts:\n"))
    a = facts["areas_with_most"][0]
    return facts, (f"There are {facts['findings']} findings. {a['lga']} has the most, with {a['findings']}. "
                   f"{facts['very_high_or_high_not_reviewed']} serious ones are not reviewed yet, so start there.")  # fmt: skip


class FromFacts(Scripted):
    """Writes a summary that uses only numbers from the facts it was given (after `bad` made-up tries)."""

    def __init__(self, bad=0, accuse=False):
        super().__init__([])
        self.bad, self.accuse, self.calls, self.facts = bad, accuse, 0, None

    def chat(self, messages, tool_specs, temperature=0):
        self.calls += 1
        self.facts, text = from_facts(messages)
        if self.accuse:
            return {"content": "This looks like rigging in " + self.facts["areas_with_most"][0]["lga"] + "."}
        if self.calls <= self.bad:
            return {"content": text + " Turnout was 987654 votes above normal."}
        return {"content": text}


def use(m):
    app.dependency_overrides[summary_route.get_model] = lambda: m
    return m


@pytest.fixture()
def cleanup_model():
    yield
    app.dependency_overrides.pop(summary_route.get_model, None)


def test_summary_is_written_from_the_counts_and_kept(api, world, analysed, cleanup_model):  # noqa: F811
    m = use(FromFacts())
    r = write(api, world)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["grounded"] and s["note"] is None and not s["stale"] and s["model"] == "scripted-test-model"
    assert s["facts"]["findings"] == m.facts["findings"] > 0 and str(m.facts["findings"]) in s["summary"]
    listed = api.get("/findings", params={"project_id": world.a.rows["projects"], "limit": 200},
                     headers=H(world.a.users["viewer"])).json()  # fmt: skip
    assert len(listed["items"]) == min(200, s["facts"]["findings"])
    got = api.get(f"/projects/{world.a.rows['projects']}/findings-summary", headers=H(world.a.users["viewer"]))
    assert got.status_code == 200 and got.json()["id"] == s["id"] and got.json()["stale"] is False
    # Hausa has its own summary; none written yet.
    assert api.get(f"/projects/{world.a.rows['projects']}/findings-summary", params={"language": "ha"},
                   headers=H(world.a.users["viewer"])).status_code == 404  # fmt: skip
    # Viewers read, they don't write; field agents and other organisations get nothing.
    assert write(api, world, "viewer").status_code == 403
    agent = member(world, "field_agent")
    assert api.get(f"/projects/{world.a.rows['projects']}/findings-summary", headers=H(agent)).status_code == 403
    assert write(api, world, "admin", org="b").status_code == 404
    with psycopg.connect(world.dsn) as c, pytest.raises(errors.InsufficientPrivilege):
        c.execute("UPDATE public.findings_summaries SET summary = 'edited' WHERE id = %s", (s["id"],))


def test_made_up_numbers_are_retried_then_flagged_and_accusations_replaced(api, world, analysed, cleanup_model):  # noqa: F811
    m = use(FromFacts(bad=1))
    s = write(api, world).json()
    assert m.calls == 2 and s["grounded"] and "987654" not in s["summary"]
    m = use(FromFacts(bad=5))
    s = write(api, world).json()
    assert m.calls == 2 and s["grounded"] is False and s["note"] == "ungrounded"
    use(FromFacts(accuse=True))
    s = write(api, world).json()
    assert s["note"] == "wording" and "rigging" not in s["summary"].lower()


def test_summary_goes_stale_when_findings_change_and_needs_a_model(api, world, analysed, cleanup_model):  # noqa: F811
    use(FromFacts())
    s = write(api, world).json()
    fid = api.get("/findings", params={"project_id": world.a.rows["projects"], "review_status": "unreviewed", "limit": 1},
                  headers=H(world.a.users["analyst"])).json()["items"][0]["id"]  # fmt: skip
    r = api.post(f"/findings/{fid}/reviews", json={"decision": "acknowledged", "reason": "Checked against the sheet."},
                 headers=H(world.a.users["analyst"], str(uuid.uuid4())))  # fmt: skip
    assert r.status_code == 201, r.text
    got = api.get(f"/projects/{world.a.rows['projects']}/findings-summary", headers=H(world.a.users["viewer"])).json()
    assert got["id"] == s["id"] and got["stale"] is True
    app.dependency_overrides[summary_route.get_model] = lambda: None
    r = write(api, world)
    assert r.status_code == 503 and "not set up" in r.json()["message"]
