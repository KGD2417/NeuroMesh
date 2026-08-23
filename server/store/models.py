"""Postgres owns what must still be true in a year.

Transient state -- the shard queue, claims, leases, live progress, encrypted
payloads -- lives in Redis and is deliberately absent here. A running job's hot
state is Redis overlaid on the SQL row at read time.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String,
    Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from common.schemas import JobStatus, ShardState
from common.tiers import Tier


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    # Only ever mutated by common.pricing.post().
    balance_credits: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = _now()

    devices: Mapped[list["Device"]] = relationship(back_populates="owner")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False, default=int(Tier.CPU_INT8))
    capability: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Plaintext exists only on the phone. We keep sha256 of it and nothing else.
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shards_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _now()

    owner: Mapped[User] = relationship(back_populates="devices")

    __table_args__ = (Index("ix_devices_owner", "owner_id"),)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    model_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    min_tier: Mapped[int] = mapped_column(Integer, nullable=False, default=int(Tier.CPU_INT8))
    shard_count: Mapped[int] = mapped_column(Integer, nullable=False)
    shard_size: Mapped[int] = mapped_column(Integer, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=JobStatus.QUEUED.value)
    cost_mc: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    spent_mc: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    result: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    shards: Mapped[list["Shard"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Shard.index"
    )

    __table_args__ = (Index("ix_jobs_owner", "owner_id"),)


class Shard(Base):
    __tablename__ = "shards"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=ShardState.QUEUED.value)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    price_mc: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    delegate: Mapped[str | None] = mapped_column(String(8))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship(back_populates="shards")

    __table_args__ = (
        UniqueConstraint("job_id", "index", name="uq_shard_job_index"),
        Index("ix_shards_device", "device_id"),
    )


class LedgerEntry(Base):
    """Append-only. Written by exactly one function: common.pricing.post()."""

    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    delta_mc: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_mc: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    ref_type: Mapped[str | None] = mapped_column(String(16))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()

    __table_args__ = (Index("ix_ledger_user_created", "user_id", "created_at"),)


class DeviceLogRow(Base):
    """Kept in SQL because 'which phone dropped which shard, and why' is the
    first question asked after a failed demo."""

    __tablename__ = "device_logs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    shard_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("shards.id", ondelete="SET NULL")
    )
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    thermal_status: Mapped[int | None] = mapped_column(Integer)
    battery_pct: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (Index("ix_device_logs_device_created", "device_id", "created_at"),)
