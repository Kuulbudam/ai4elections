"""Authentication and authorisation for FastAPI endpoints.

* Verifies the Supabase access token sent as `Authorization: Bearer <jwt>`.
  - Asymmetric keys (ES256/RS256, current Supabase default): public keys from the project's JWKS
    endpoint, cached for 10 minutes.
  - Legacy HS256: only if SUPABASE_JWT_SECRET is configured.
  - Always checks signature, expiry, audience "authenticated" and issuer.
* Looks up the caller's role in the target organisation from the database (never from the token
  or the request body) and enforces the role list documented per operation in openapi.yaml.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request

ASYMMETRIC_ALGS = ["ES256", "RS256"]
JWKS_TTL_SECONDS = 600


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None
    claims: dict[str, Any]


class JwksCache:
    def __init__(self) -> None:
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def fetch(self, url: str) -> dict[str, Any]:  # separated so tests can replace it
        r = httpx.get(url, timeout=5)
        r.raise_for_status()
        return r.json()

    def key(self, url: str, kid: str | None) -> jwt.PyJWK:
        with self._lock:
            stale = time.monotonic() - self._fetched_at > JWKS_TTL_SECONDS
            if stale or (kid not in self._keys):
                jwks = jwt.PyJWKSet.from_dict(self.fetch(url))
                self._keys = {k.key_id: k for k in jwks.keys if k.key_id}
                self._fetched_at = time.monotonic()
            if kid not in self._keys:
                raise jwt.InvalidKeyError("unknown signing key")
            return self._keys[kid]


JWKS = JwksCache()


def _unauthenticated(message: str = "Missing or invalid access token") -> HTTPException:
    return HTTPException(status_code=401, detail=message)


def verify_token(token: str) -> CurrentUser:
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not base:
        raise HTTPException(status_code=500, detail="server not configured")
    issuer = f"{base}/auth/v1"
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg")
        if alg in ASYMMETRIC_ALGS:
            key = JWKS.key(f"{issuer}/.well-known/jwks.json", header.get("kid")).key
            algs = [alg]
        elif alg == "HS256" and os.environ.get("SUPABASE_JWT_SECRET"):
            key = os.environ["SUPABASE_JWT_SECRET"]
            algs = ["HS256"]
        else:
            raise jwt.InvalidAlgorithmError(f"algorithm not allowed: {alg}")
        claims = jwt.decode(
            token,
            key=key,
            algorithms=algs,
            audience="authenticated",
            issuer=issuer,
            options={"require": ["exp", "sub", "aud", "iss"]},
            leeway=10,
        )
    except (jwt.PyJWTError, httpx.HTTPError, ValueError) as exc:
        raise _unauthenticated() from exc
    if claims.get("role") != "authenticated":
        raise _unauthenticated()
    return CurrentUser(id=str(claims["sub"]), email=claims.get("email"), claims=claims)


def current_user(request: Request) -> CurrentUser:
    """FastAPI dependency."""
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthenticated()
    return verify_token(token.strip())


def member_role(conn, org_id: str, user_id: str) -> str | None:
    row = conn.execute(
        "SELECT role FROM public.memberships WHERE org_id = %s AND user_id = %s", (org_id, user_id)
    ).fetchone()
    if row is None:
        return None
    return row["role"] if isinstance(row, dict) else row[0]


def require_role(conn, org_id: str, user: CurrentUser, allowed: set[str]) -> str:
    """Return the caller's role in org_id, or raise 403. Non-members also get 403 (never 200)."""
    role = member_role(conn, org_id, user.id)
    if role is None or role not in allowed:
        raise HTTPException(status_code=403, detail="You do not have permission for this action")
    return role
