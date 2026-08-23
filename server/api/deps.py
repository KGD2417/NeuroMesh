"""Shared FastAPI dependencies: who is calling, and may they call this often.

Ownership rule, applied everywhere: someone else's job or device is a 404, not
a 403. A 403 confirms the resource exists, which is a slow enumeration oracle
for job and device IDs.
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api import security
from config import get_settings
from store.db import get_session
from store.models import Device, User
from store.redis_client import redis

Session = Annotated[AsyncSession, Depends(get_session)]

NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "not found")


async def current_user(
    session: Session,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    user_id = security.decode(authorization.split(" ", 1)[1].strip(), "access")
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown account")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def current_device(
    session: Session,
    x_device_key: Annotated[str | None, Header()] = None,
) -> Device:
    """Phones authenticate with their device key, never with the owner's JWT.
    A stolen phone must be revocable without locking the owner out."""
    if not x_device_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing device key")
    digest = security.hash_device_key(x_device_key)
    device = (
        await session.execute(select(Device).where(Device.api_key_hash == digest))
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown device key")
    return device


CurrentDevice = Annotated[Device, Depends(current_device)]


# --- rate limiting ----------------------------------------------------------

async def _hit(bucket: str, limit: int, window_s: int) -> None:
    key = f"nm:rl:{bucket}:{int(time.time()) // window_s}"
    r = redis()
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, window_s * 2)
    if count > limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate limit exceeded",
            headers={"Retry-After": str(window_s)},
        )


async def rate_limit_anonymous(request: Request) -> None:
    """Per-IP, and only for calls with no account behind them yet."""
    limit, window = get_settings().rate_limit_anonymous
    ip = request.client.host if request.client else "unknown"
    await _hit(f"ip:{ip}", limit, window)


async def rate_limit_account(user: CurrentUser) -> User:
    """Per-account for authenticated actions: a shared office NAT must not
    rate-limit its own users into each other."""
    limit, window = get_settings().rate_limit_account
    await _hit(f"acct:{user.id}", limit, window)
    return user


RateLimitedUser = Annotated[User, Depends(rate_limit_account)]


async def rate_limit_device(device: CurrentDevice) -> Device:
    limit, window = get_settings().rate_limit_account
    await _hit(f"dev:{device.id}", limit, window)
    return device


RateLimitedDevice = Annotated[Device, Depends(rate_limit_device)]


def parse_uuid_or_404(value: str) -> uuid.UUID:
    """A malformed ID is indistinguishable from someone else's ID: 404 both."""
    try:
        return uuid.UUID(value)
    except ValueError:
        raise NOT_FOUND from None
