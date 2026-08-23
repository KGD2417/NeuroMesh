"""register / login / refresh."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api import security
from api.deps import Session, rate_limit_anonymous
from common import pricing
from store.models import User

router = APIRouter(
    prefix="/auth", tags=["auth"], dependencies=[Depends(rate_limit_anonymous)]
)


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str


class RefreshRequest(BaseModel):
    refresh_token: str


def _pair(user: User) -> TokenPair:
    return TokenPair(
        access_token=security.access_token(user.id),
        refresh_token=security.refresh_token(user.id),
        user_id=str(user.id),
    )


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(body: Credentials, session: Session) -> TokenPair:
    user = User(
        email=body.email.lower(),
        password_hash=security.hash_password(body.password),
        balance_credits=0,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from None

    # Seed credits so a new account can submit a job at the demo without
    # first having to earn any. Goes through the ledger like everything else.
    await pricing.post(
        session,
        user_id=user.id,
        delta_mc=pricing.SIGNUP_GRANT_MC,
        kind=pricing.EntryKind.SIGNUP_GRANT,
        ref_type="user",
        ref_id=str(user.id),
    )
    return _pair(user)


@router.post("/login", response_model=TokenPair)
async def login(body: Credentials, session: Session) -> TokenPair:
    user = (
        await session.execute(select(User).where(User.email == body.email.lower()))
    ).scalar_one_or_none()
    # Same error and roughly the same work either way: do not leak which
    # half was wrong.
    if user is None or not security.verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    return _pair(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, session: Session) -> TokenPair:
    user_id = security.decode(body.refresh_token, "refresh")
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown account")
    return _pair(user)
