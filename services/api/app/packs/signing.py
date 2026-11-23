"""Ed25519 signing key for submission packs.

The private key is SUBMISSION_SIGNING_KEY (32 random bytes, base64) when set. Otherwise it is derived with HKDF from
the server's service-role secret, so every deployment that shares that secret signs with the same key. The public
key is published at GET /signing-key and inside every pack; key_id is the first 16 hex characters of its SHA-256.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

INFO = b"eaas submission pack signing key v1"


class SigningNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class Key:
    private: Ed25519PrivateKey
    public_b64: str
    key_id: str


def _seed() -> bytes:
    if raw := os.environ.get("SUBMISSION_SIGNING_KEY", "").strip():
        seed = base64.b64decode(raw)
        if len(seed) != 32:
            raise SigningNotConfigured("SUBMISSION_SIGNING_KEY must be 32 bytes, base64-encoded.")
        return seed
    secret = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not secret:
        raise SigningNotConfigured("No signing key is set up on this server.")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=INFO).derive(secret.encode())


def public_raw(pub: Ed25519PublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id_of(public_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(public_b64)).hexdigest()[:16]


def load() -> Key:
    priv = Ed25519PrivateKey.from_private_bytes(_seed())
    pub = base64.b64encode(public_raw(priv.public_key())).decode()
    return Key(priv, pub, key_id_of(pub))


def sign(key: Key, data: bytes) -> str:
    return base64.b64encode(key.private.sign(data)).decode()


def verify(public_b64: str, data: bytes, signature_b64: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64)).verify(base64.b64decode(signature_b64), data)
        return True
    except (InvalidSignature, ValueError):
        return False
