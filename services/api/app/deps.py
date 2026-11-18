"""Request-scoped dependencies: database, storage, dispatch, rate limiting, tenancy checks."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
from fastapi import Depends, HTTPException, Request
from psycopg.rows import dict_row

from app.auth import CurrentUser, current_user, member_role
from app.storage import Storage, SupabaseStorage

log = logging.getLogger("ai4e.api")

USER_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_USER_PER_MIN", "60"))
IP_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_IP_PER_MIN", "120"))


def get_db() -> Iterator[psycopg.Connection]:
    # prepare_threshold=None: safe behind the Supabase transaction pooler (port 6543)
    conn = psycopg.connect(
        os.environ["DATABASE_URL"],
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=None,
        connect_timeout=10,
    )
    try:
        yield conn
    finally:
        conn.close()


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = SupabaseStorage()
    return _storage


class Dispatcher:
    """Wakes the GitHub Actions worker. Best effort: the 15-min sweep is the fallback (ADR-0002)."""

    def __call__(self, job_id: str) -> bool:
        token = os.environ.get("GITHUB_DISPATCH_TOKEN")
        repo = os.environ.get("GITHUB_REPOSITORY")
        if not token or not repo:
            return False
        try:
            r = httpx.post(
                f"https://api.github.com/repos/{repo}/dispatches",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                json={"event_type": "ai4e-job", "client_payload": {"job_id": job_id}},
                timeout=10,
            )
            if r.status_code >= 300:
                log.warning("dispatch failed: HTTP %s", r.status_code)
            return r.status_code < 300
        except httpx.HTTPError:
            log.warning("dispatch failed: network error")
            return False


def get_dispatcher() -> Dispatcher:
    return Dispatcher()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def rate_limited_user(
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: psycopg.Connection = Depends(get_db),
) -> CurrentUser:
    """Authenticated user, after per-user and per-IP rate limits (shared across instances via Postgres)."""
    for key, limit in (
        (f"user:{user.id}", USER_LIMIT_PER_MIN),
        (f"ip:{client_ip(request)}", IP_LIMIT_PER_MIN),
    ):
        ok = db.execute("SELECT app.rate_limit_hit(%s, %s, 60) AS ok", (key, limit)).fetchone()["ok"]
        if not ok:
            raise HTTPException(status_code=429, detail="Too many requests. Wait a minute and try again.")
    return user


def authorize(db: psycopg.Connection, org_id: str, user: CurrentUser, allowed: set[str]) -> str:
    """Non-members get 404 (existence is not revealed); members without the role get 403."""
    role = member_role(db, org_id, user.id)
    if role is None:
        raise HTTPException(status_code=404, detail="Not found")
    if role not in allowed:
        raise HTTPException(status_code=403, detail="You do not have permission for this action")
    return role


def act_as(db: psycopg.Connection, user: CurrentUser) -> None:
    """Record the acting user for audit triggers inside the current transaction."""
    db.execute("SELECT set_config('app.actor_id', %s, true)", (user.id,))


def row_or_404(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row
