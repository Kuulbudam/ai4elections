import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import auth
from app.main import http_error

BASE = "https://proj.supabase.test"
ISS = f"{BASE}/auth/v1"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", BASE)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    auth.JWKS._keys = {}
    auth.JWKS._fetched_at = 0.0


@pytest.fixture()
def signing_key(monkeypatch):
    private = ec.generate_private_key(ec.SECP256R1())
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"kid": "k1", "alg": "ES256", "use": "sig"})
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return {"keys": [jwk]}

    monkeypatch.setattr(auth.JWKS, "fetch", fake_fetch)
    return private, calls


def make(private, **over):
    claims = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "aud": "authenticated",
        "role": "authenticated",
        "iss": ISS,
        "exp": int(time.time()) + 600,
        "email": "a@example.com",
        "aal": "aal2",
    }
    claims.update(over)
    return jwt.encode(claims, private, algorithm="ES256", headers={"kid": over.pop("kid", "k1")})


def test_valid_es256_token(signing_key):
    private, calls = signing_key
    user = auth.verify_token(make(private))
    assert user.id == "11111111-1111-1111-1111-111111111111"
    assert calls == [f"{ISS}/.well-known/jwks.json"]
    auth.verify_token(make(private))
    assert len(calls) == 1, "JWKS is cached"


@pytest.mark.parametrize(
    "override",
    [
        {"exp": int(time.time()) - 60},
        {"aud": "anon"},
        {"iss": "https://evil.example/auth/v1"},
        {"role": "anon"},
        {"role": "service_role"},
        {"aal": "aal1"},
    ],
)
def test_rejects_bad_claims(signing_key, override):
    private, _ = signing_key
    with pytest.raises(auth.HTTPException) as e:
        auth.verify_token(make(private, **override))
    assert e.value.status_code == 401


def test_rejects_token_signed_by_another_key(signing_key):
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(auth.HTTPException):
        auth.verify_token(make(other))


def test_rejects_unknown_kid(signing_key):
    private, _ = signing_key
    token = jwt.encode(
        {
            "sub": "x",
            "aud": "authenticated",
            "role": "authenticated",
            "iss": ISS,
            "exp": int(time.time()) + 60,
        },
        private,
        algorithm="ES256",
        headers={"kid": "nope"},
    )
    with pytest.raises(auth.HTTPException):
        auth.verify_token(token)


def test_rejects_alg_none_and_hs256_without_secret():
    unsigned = jwt.encode(
        {
            "sub": "x",
            "aud": "authenticated",
            "role": "authenticated",
            "iss": ISS,
            "exp": int(time.time()) + 60,
        },
        key=None,
        algorithm="none",
    )
    with pytest.raises(auth.HTTPException):
        auth.verify_token(unsigned)
    hs = jwt.encode(
        {
            "sub": "x",
            "aud": "authenticated",
            "role": "authenticated",
            "iss": ISS,
            "exp": int(time.time()) + 60,
        },
        "guessable-secret-of-at-least-32-bytes!!",
        algorithm="HS256",
    )
    with pytest.raises(auth.HTTPException):
        auth.verify_token(hs)


def test_hs256_accepted_only_with_configured_secret(monkeypatch):
    secret = "legacy-project-secret-at-least-32-bytes-long"
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    claims = {
        "sub": "u1",
        "aud": "authenticated",
        "role": "authenticated",
        "iss": ISS,
        "exp": int(time.time()) + 60,
        "aal": "aal2",
    }
    assert auth.verify_token(jwt.encode(claims, secret, algorithm="HS256")).id == "u1"
    with pytest.raises(auth.HTTPException):
        auth.verify_token(jwt.encode(claims, "x" * 44, algorithm="HS256"))


def test_dependency_returns_401_contract_error(signing_key):
    private, _ = signing_key
    app = FastAPI()
    app.add_exception_handler(auth.HTTPException, http_error)

    @app.get("/whoami")
    def whoami(user: auth.CurrentUser = Depends(auth.current_user)):
        return {"id": user.id}

    c = TestClient(app)
    assert c.get("/whoami").json()["code"] == "UNAUTHENTICATED"
    assert c.get("/whoami", headers={"Authorization": "Basic abc"}).status_code == 401
    ok = c.get("/whoami", headers={"Authorization": f"Bearer {make(private)}"})
    assert ok.status_code == 200 and ok.json()["id"].startswith("1111")
