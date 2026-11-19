"""GET /areas: totals equal a direct calculation from the raw synthetic file (Phase 6 acceptance)."""

from __future__ import annotations

import csv
from collections import defaultdict

from .test_analysis_job import _analyse, _validate
from .test_ingestion import REPO, H, drain
from .test_validation_job import ingested  # noqa: F401


def _num(s: str) -> int | None:
    s = s.strip()
    return int(s) if s.isdigit() else None


def direct(level: str):
    """Independent calculation straight from the CSV cells and the ground-truth rule labels.

    Exclusions follow the published rules: a row with a missing PU code or label, or a duplicate code
    (V010), counts for nothing; V006 or an unreadable registered/accredited count removes it from turnout;
    V007/V008/V009 or an unreadable valid/party count removes it from vote share."""
    rows = list(csv.DictReader((REPO / "data/synthetic/SYNTHETIC_results_seed2026.csv").open()))
    truth = list(csv.DictReader((REPO / "data/synthetic/SYNTHETIC_ground_truth_seed2026.csv").open()))
    keys = {"state": ["State"], "lga": ["State", "LGA"], "ward": ["State", "LGA", "Ward"]}[level]
    out: dict[tuple, dict] = defaultdict(
        lambda: {"units": 0, "t_units": 0, "reg": 0, "acc": 0, "s_units": 0, "val": 0, "parties": defaultdict(int)}
    )
    for r, t in zip(rows, truth, strict=True):
        a = out[tuple(" ".join(r[c].split()) or None for c in keys)]
        a["units"] += 1
        rules = set(filter(None, t["expected_rules"].split("|")))
        whole_row_out = "V010" in rules or any(not r[c].strip() for c in ("PU Code", "State", "LGA", "Ward"))
        reg, acc, val = _num(r["Registered Voters"]), _num(r["Accredited Voters"]), _num(r["Valid Votes"])
        votes = {p: _num(r[f"Votes {p}"]) for p in ("PA", "PB", "PC", "PD", "PE")}
        if not whole_row_out and "V006" not in rules and reg and acc is not None:
            a["t_units"] += 1
            a["reg"] += reg
            a["acc"] += acc
        if not (whole_row_out or rules & {"V007", "V008", "V009"} or not val or None in votes.values()):
            a["s_units"] += 1
            a["val"] += val
            for p, n in votes.items():
                a["parties"][p] += n
    return out


def test_area_totals_match_direct_calculation(api, world, ingested):  # noqa: F811
    _validate(api, world, ingested)
    assert _analyse(api, world, ingested).status_code == 202
    drain(world)
    hv = H(world.a.users["viewer"])
    for level in ("state", "lga", "ward"):
        got = api.get("/areas", params={"dataset_version_id": ingested, "level": level}, headers=hv).json()
        want = direct(level)
        keys = {"state": ["state"], "lga": ["state", "lga"], "ward": ["state", "lga", "ward"]}[level]
        assert len(got["items"]) == len(want), level
        for it in got["items"]:
            w = want[tuple(it[k] for k in keys)]
            assert it["units"] == w["units"], (level, it)
            assert (it["turnout"]["units"], it["turnout"]["registered_voters"], it["turnout"]["accredited_voters"]) == (
                w["t_units"], w["reg"], w["acc"]), (level, it)  # fmt: skip
            assert it["turnout"]["rate"] == (f"{w['acc'] / w['reg']:.6f}" if w["reg"] else None)
            assert (it["vote_share"]["units"], it["vote_share"]["valid_votes"]) == (w["s_units"], w["val"]), (level, it)
            assert it["vote_share"]["party_totals"] == (dict(sorted(w["parties"].items())) if w["s_units"] else {})
    # findings counted per area add up to the run's totals
    st = api.get("/areas", params={"dataset_version_id": ingested, "level": "state"}, headers=hv).json()
    assert sum(i["findings"]["unusual_turnout"] for i in st["items"]) == 49
    assert sum(i["findings"]["unusual_share"] for i in st["items"]) == 91
    assert sum(i["units"] for i in st["items"]) == 12_023


def test_drill_down_and_reference_flags(api, world, ingested):  # noqa: F811
    _validate(api, world, ingested)
    hv = H(world.a.users["viewer"])
    st = api.get("/areas", params={"dataset_version_id": ingested, "level": "state"}, headers=hv).json()
    first = next(i for i in st["items"] if i["in_reference_list"])
    lgas = api.get("/areas", params={"dataset_version_id": ingested, "level": "lga", "state": first["state"].upper()},
                   headers=hv).json()  # fmt: skip
    assert lgas["items"] and all(i["state"] == first["state"] for i in lgas["items"])
    assert sum(i["units"] for i in lgas["items"]) == first["units"]
    wards = api.get("/areas", params={"dataset_version_id": ingested, "level": "ward", "state": first["state"],
                                      "lga": lgas["items"][0]["lga"]}, headers=hv).json()  # fmt: skip
    assert sum(i["units"] for i in wards["items"]) == lgas["items"][0]["units"]
    # the planted unmatched labels appear as themselves, flagged as not in the reference list (never "fixed")
    allw = api.get("/areas", params={"dataset_version_id": ingested, "level": "ward"}, headers=hv).json()["items"]
    assert any(i["in_reference_list"] is False for i in allw)
    assert api.get("/areas", params={"dataset_version_id": ingested, "level": "lga", "lga": "x"},
                   headers=hv).status_code == 400  # fmt: skip


def test_areas_permissions_and_preconditions(api, world, ingested):  # noqa: F811
    p = {"dataset_version_id": ingested, "level": "state"}
    assert api.get("/areas", params=p, headers=H(world.a.users["viewer"])).status_code == 409  # not validated
    assert api.get("/areas", params=p, headers=H(world.b.users["admin"])).status_code == 404
    assert api.get("/areas", params={**p, "level": "country"}, headers=H(world.a.users["viewer"])).status_code == 400
