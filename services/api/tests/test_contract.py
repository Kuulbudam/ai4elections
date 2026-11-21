"""Contract tests: the OpenAPI spec and JSON schemas are valid and follow project conventions."""

import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from openapi_spec_validator import validate

CONTRACTS = Path(__file__).resolve().parents[3] / "packages" / "contracts"
SPEC = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text())
ROLES = {"admin", "analyst", "researcher", "viewer", "coordinator", "field_agent"}
REQUIRED_PATHS = {
    ("post", "/uploads"),
    ("post", "/dataset-versions/{id}/finalize"),
    ("put", "/dataset-versions/{id}/mapping"),
    ("post", "/jobs"),
    ("get", "/jobs/{id}"),
    ("get", "/findings"),
    ("post", "/findings/{id}/reviews"),
    ("post", "/reports"),
    ("get", "/reports/{id}/export"),
}


def _operations():
    for path, item in SPEC["paths"].items():
        for method, op in item.items():
            yield method, path, op


def test_spec_is_valid_openapi_31():
    validate(SPEC)
    assert SPEC["openapi"].startswith("3.1")


def test_all_required_endpoints_present():
    present = {(m, p) for m, p, _ in _operations()}
    assert REQUIRED_PATHS <= present


@pytest.mark.parametrize("method,path,op", list(_operations()), ids=lambda x: str(x)[:40])
def test_operation_conventions(method, path, op):
    assert op.get("operationId")
    if path == "/health":
        assert op.get("security") == []
        return
    roles = op.get("x-required-roles")
    assert roles and set(roles) <= ROLES, "every protected operation documents its roles"
    codes = set(op["responses"])
    assert {"401", "403", "429", "500"} <= codes, "auth, rate-limit and internal errors documented"
    if method == "post":
        assert op.get("x-idempotent") is True


def test_review_roles_exclude_researcher_and_viewer():
    roles = SPEC["paths"]["/findings/{id}/reviews"]["post"]["x-required-roles"]
    assert "researcher" not in roles and "viewer" not in roles


def test_upload_size_limit_is_25_mib():
    size = SPEC["components"]["schemas"]["UploadRequest"]["properties"]["size_bytes"]
    assert size["maximum"] == 25 * 1024 * 1024


def test_no_fraud_wording_in_contract():
    text = (CONTRACTS / "openapi.yaml").read_text().lower()
    allowed = "never fraud verdicts"
    assert text.replace(allowed, "").count("fraud") == 0


def _schema(name):
    return json.loads((CONTRACTS / "schemas" / name).read_text())


def test_json_schemas_are_valid():
    for f in (CONTRACTS / "schemas").glob("*.json"):
        jsonschema.Draft202012Validator.check_schema(json.loads(f.read_text()))


def test_canonical_record_accepts_good_and_rejects_bad():
    v = jsonschema.Draft202012Validator(_schema("canonical-record.schema.json"))
    good = {
        "schema_version": "1.0.0",
        "raw_row_id": "6f1c2b1e-0a3e-4b52-9a51-0c6a4c0c0d11",
        "source_row_number": 2,
        "pu_code": "SY-01-02-003",
        "state": "Synthetica",
        "lga": "North Vale",
        "ward": "Ward 3",
        "registered_voters": 800,
        "accredited_voters": 410,
        "valid_votes": 400,
        "rejected_votes": 10,
        "total_cast": 410,
        "party_votes": {"PA": 250, "PB": 150},
    }
    assert not list(v.iter_errors(good))
    assert list(v.iter_errors({**good, "registered_voters": -1}))
    assert list(v.iter_errors({**good, "latitude": 9.0}))  # lat without lon
    assert list(v.iter_errors({**good, "party_votes": {"bad code": 1}}))


def test_pyproject_dependencies_match_requirements():
    """Vercel installs from pyproject.toml; CI and the worker from requirements.txt. They must agree."""
    import tomllib

    api = Path(__file__).resolve().parents[1]
    deps = tomllib.loads((api / "pyproject.toml").read_text())["project"]["dependencies"]
    reqs = [
        line.strip()
        for line in (api / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert sorted(d.lower() for d in deps) == sorted(r.lower() for r in reqs)
