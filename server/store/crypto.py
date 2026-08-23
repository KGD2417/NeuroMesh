"""Shard payloads are encrypted at rest in Redis.

A queued shard's inputs may sit in Redis for minutes across a fleet of phones
we do not own. They are sealed here and opened only inside the claim path, for
the one device that actually holds the lease. A status poll never decrypts.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from config import get_settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(get_settings().payload_key.encode())


def seal(items: list[Any]) -> bytes:
    return _fernet().encrypt(json.dumps(items, separators=(",", ":")).encode())


def open_(blob: bytes) -> list[Any]:
    try:
        return json.loads(_fernet().decrypt(blob))
    except InvalidToken as exc:  # key rotated out from under a queued job
        raise PayloadUnreadable("shard payload cannot be decrypted") from exc


class PayloadUnreadable(Exception):
    pass
