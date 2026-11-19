"""GET /dataset-versions/{id}/dashboard agrees with the analysis run."""

from __future__ import annotations

from .test_analysis_job import _analyse, _validate
from .test_ingestion import H, drain
from .test_validation_job import ingested  # noqa: F401


def test_dashboard_matches_the_run(api, world, ingested):  # noqa: F811
    hv = H(world.a.users["viewer"])
    assert api.get(f"/dataset-versions/{ingested}/dashboard", headers=hv).status_code == 409  # not validated
    _validate(api, world, ingested)
    pre = api.get(f"/dataset-versions/{ingested}/dashboard", headers=hv).json()
    assert pre["analytical_run_id"] is None and pre["flagged"] == [] and len(pre["units"]) == 12_023
    assert _analyse(api, world, ingested).status_code == 202
    drain(world)
    d = api.get(f"/dataset-versions/{ingested}/dashboard", headers=hv).json()
    assert len(d["units"]) == 12_023
    assert d["kpi"]["turnout_counted"] == 11_990 and d["kpi"]["share_counted"] == 11_979  # engine's eligible counts
    assert d["kpi"]["unusual_turnout"] == 49 and d["kpi"]["unusual_share"] == 91 and len(d["flagged"]) == 140
    assert d["kpi"]["data_quality"] == 79 and sum(d["data_quality_by_rule"].values()) == 79
    assert d["severity"]["turnout_rate"]["very_high"] == 15
    assert all(0 <= u[0] < len(d["lgas"]) for u in d["units"])
    assert all(0 <= u[1] <= 1 for u in d["units"] if u[1] is not None)
    assert all(abs(f["robust_z"]) >= 3.5 and d["lgas"][f["lga"]] for f in d["flagged"])
    assert api.get(f"/dataset-versions/{ingested}/dashboard", headers=H(world.b.users["admin"])).status_code == 404
