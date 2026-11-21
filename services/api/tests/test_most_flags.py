from app.assistant.tools import _most_flags


def test_most_flags_names_top_area_and_ties():
    rows = [
        {"state": "Eastmarch", "findings": {"unusual_turnout": 11, "unusual_share": 4, "data_quality": 2}},
        {"state": "Norvale", "findings": {"unusual_turnout": 15, "unusual_share": 4}},
        {"state": "Quillon", "findings": None},
    ]
    out = _most_flags(rows, "state")
    assert out["turnout"] == {"areas": ["Norvale"], "count": 15}
    assert out["share"] == {"areas": ["Eastmarch", "Norvale"], "count": 4}
    assert out["data_quality"] == {"areas": ["Eastmarch"], "count": 2}
    assert "very_high" not in out


def test_most_flags_empty():
    assert _most_flags([], "lga") == {}
