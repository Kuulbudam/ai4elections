"""Supabase Storage access with the service key (server and worker only)."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import quote

import httpx

BUCKET = "datasets"
SIGNED_UPLOAD_TTL = timedelta(hours=2)  # fixed by Supabase for signed upload URLs


@dataclass(frozen=True)
class SignedUpload:
    url: str
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class StoredObject:
    size: int
    sha256: str
    head: bytes  # first 64 KiB, for cheap content checks


class Storage(Protocol):
    def create_signed_upload(self, path: str) -> SignedUpload: ...
    def inspect(self, path: str) -> StoredObject | None: ...
    def download(self, path: str) -> bytes | None: ...


class SupabaseStorage:
    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        self.base = (url or os.environ["SUPABASE_URL"]).rstrip("/") + "/storage/v1"
        key = key or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        self.headers = {"apikey": key}
        if key.startswith("eyJ"):
            self.headers["Authorization"] = f"Bearer {key}"

    def _obj(self, path: str) -> str:
        return f"{BUCKET}/{quote(path)}"

    def create_signed_upload(self, path: str) -> SignedUpload:
        r = httpx.post(f"{self.base}/object/upload/sign/{self._obj(path)}", headers=self.headers, timeout=15)
        r.raise_for_status()
        rel = r.json()["url"]  # "/object/upload/sign/datasets/<path>?token=..."
        token = httpx.URL(rel).params.get("token", "")
        return SignedUpload(url=f"{self.base}{rel}", token=token, expires_at=datetime.now(UTC) + SIGNED_UPLOAD_TTL)

    def inspect(self, path: str) -> StoredObject | None:
        h = hashlib.sha256()
        size = 0
        head = b""
        with httpx.stream("GET", f"{self.base}/object/{self._obj(path)}", headers=self.headers, timeout=120) as r:
            if r.status_code in (400, 404):
                return None
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 16):
                if len(head) < 65536:
                    head += chunk[: 65536 - len(head)]
                h.update(chunk)
                size += len(chunk)
        return StoredObject(size=size, sha256=h.hexdigest(), head=head)

    def download(self, path: str) -> bytes | None:
        r = httpx.get(f"{self.base}/object/{self._obj(path)}", headers=self.headers, timeout=120)
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        return r.content


class MemoryStorage:
    """In-memory stand-in used by tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.signed: list[str] = []

    def create_signed_upload(self, path: str) -> SignedUpload:
        self.signed.append(path)
        return SignedUpload(
            url=f"https://storage.test/upload/{path}?token=t",
            token="t",  # noqa: S106 - test double
            expires_at=datetime.now(UTC) + SIGNED_UPLOAD_TTL,
        )

    def put(self, path: str, data: bytes) -> None:
        if path in self.objects:
            raise FileExistsError(path)  # mirrors Supabase: no overwrite without upsert
        self.objects[path] = data

    def inspect(self, path: str) -> StoredObject | None:
        data = self.objects.get(path)
        if data is None:
            return None
        return StoredObject(size=len(data), sha256=hashlib.sha256(data).hexdigest(), head=data[:65536])

    def download(self, path: str) -> bytes | None:
        return self.objects.get(path)
