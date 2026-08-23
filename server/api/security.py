"""Passwords, tokens, device keys, pairing codes.

Password hashing is stdlib scrypt -- there is no reason to add a dependency for
something hashlib already does correctly. Device API keys are 256 bits of
os.urandom, so they get a plain SHA-256: a slow KDF buys nothing against a key
that was never guessable in the first place. The plaintext key exists only on
the phone; the server stores the digest and can never recover it.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from config import get_settings

_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, actual)


# --- JWTs -------------------------------------------------------------------

def _token(sub: str, kind: str, ttl_s: int) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": sub,
            "typ": kind,
            "iat": now,
            "exp": now + timedelta(seconds=ttl_s),
            "jti": secrets.token_urlsafe(8),
        },
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )


def access_token(user_id: uuid.UUID) -> str:
    return _token(str(user_id), "access", get_settings().access_token_ttl_s)


def refresh_token(user_id: uuid.UUID) -> str:
    return _token(str(user_id), "refresh", get_settings().refresh_token_ttl_s)


def decode(token: str, expect: str) -> uuid.UUID | None:
    s = get_settings()
    try:
        claims = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if claims.get("typ") != expect:
        return None
    try:
        return uuid.UUID(claims["sub"])
    except (KeyError, ValueError):
        return None


# --- device keys ------------------------------------------------------------

def new_device_key() -> tuple[str, str]:
    """(plaintext, sha256 hex). The plaintext is returned exactly once, to the
    phone that just paired, and is never stored."""
    plaintext = "nmd_" + secrets.token_urlsafe(32)
    return plaintext, hash_device_key(plaintext)


def hash_device_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


# --- pairing codes ----------------------------------------------------------

_PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1


def new_pairing_code() -> str:
    """Short, short-lived, and typed in by a human on a phone screen."""
    return "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(8))
