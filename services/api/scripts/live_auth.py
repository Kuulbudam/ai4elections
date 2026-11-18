"""Shared helper for live checks: complete Supabase two-step verification (TOTP) for a test user.

Implements RFC 6238 TOTP with the standard library, enrols an authenticator factor through the
Supabase Auth API, then challenges and verifies it, returning an aal2 access token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

import httpx


def totp(secret_b32: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = int((at if at is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def upgrade_to_aal2(http: httpx.Client, supabase_url: str, anon_key: str, access_token: str) -> str:
    """Enrol a TOTP factor for the signed-in user and verify it. Returns the new aal2 access token."""
    base = supabase_url.rstrip("/") + "/auth/v1"
    headers = {"apikey": anon_key, "Authorization": f"Bearer {access_token}"}
    f = http.post(f"{base}/factors", headers=headers, json={"factor_type": "totp", "friendly_name": "ci"})
    f.raise_for_status()
    factor = f.json()
    ch = http.post(f"{base}/factors/{factor['id']}/challenge", headers=headers)
    ch.raise_for_status()
    v = http.post(
        f"{base}/factors/{factor['id']}/verify",
        headers=headers,
        json={"challenge_id": ch.json()["id"], "code": totp(factor["totp"]["secret"])},
    )
    v.raise_for_status()
    return v.json()["access_token"]
