"""Idempotency-Key support (openapi: x-idempotent operations).

Same user + same key + same request body → the stored response is replayed.
Same key with a different body → 409 CONFLICT.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb


def request_hash(endpoint: str, body: Any) -> str:
    canonical = json.dumps(jsonable_encoder(body), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{endpoint}\n{canonical}".encode()).hexdigest()


def replay(db: psycopg.Connection, user_id: str, key: str | None, endpoint: str, body: Any) -> JSONResponse | None:
    if not key:
        return None
    row = db.execute(
        "SELECT endpoint, request_hash, status_code, response FROM app.idempotency_keys WHERE user_id=%s AND key=%s",
        (user_id, key),
    ).fetchone()
    if row is None:
        return None
    if row["endpoint"] != endpoint or row["request_hash"] != request_hash(endpoint, body):
        raise HTTPException(status_code=409, detail="This Idempotency-Key was already used for a different request")
    return JSONResponse(status_code=row["status_code"], content=row["response"])


def remember(
    db: psycopg.Connection,
    user_id: str,
    key: str | None,
    endpoint: str,
    body: Any,
    status: int,
    response: Any,
) -> None:
    if not key:
        return
    db.execute(
        """INSERT INTO app.idempotency_keys (user_id, key, endpoint, request_hash, status_code, response)
           VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (user_id, key) DO NOTHING""",
        (user_id, key, endpoint, request_hash(endpoint, body), status, Jsonb(jsonable_encoder(response))),
    )
