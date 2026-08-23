"""Wire schemas shared by the orchestrator and the phone.

The phone never receives code -- it receives one of these, and runs a
pre-registered model graph in a fixed interpreter against it.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from common.tiers import Capability, Tier


class ShardState(str, enum.Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# --- what a phone is handed -------------------------------------------------

class ShardAssignment(BaseModel):
    """The complete work order. `items` is the decrypted payload; it is only
    ever produced by a successful claim by the device that holds the lease."""

    shard_id: str
    job_id: str
    index: int
    model_ref: str
    tier: Tier
    items: list[Any]
    lease_deadline_ms: int
    lease_ttl_s: int


class ShardResult(BaseModel):
    """What the phone posts back. `outputs` must line up 1:1 with `items`."""

    outputs: list[Any]
    duration_ms: int = Field(ge=0)
    delegate: Literal["qnn", "gpu", "cpu"]
    device_logs: list["DeviceLog"] = Field(default_factory=list)

    @field_validator("outputs")
    @classmethod
    def _not_empty(cls, v: list[Any]) -> list[Any]:
        if not v:
            raise ValueError("a shard result must contain at least one output")
        return v


class ShardFailure(BaseModel):
    reason: str = Field(max_length=500)
    retryable: bool = True


class DeviceLog(BaseModel):
    """Structured line from the phone. Free-text is capped; the fleet is large
    and logs are the one thing a hostile device can spam us with."""

    ts: datetime
    level: Literal["debug", "info", "warn", "error"]
    event: str = Field(max_length=64)
    detail: str | None = Field(default=None, max_length=500)
    thermal_status: int | None = Field(default=None, ge=0, le=6)
    battery_pct: int | None = Field(default=None, ge=0, le=100)


# --- job submission ---------------------------------------------------------

class JobSubmit(BaseModel):
    model_ref: str = Field(min_length=1, max_length=128)
    inputs: list[Any] = Field(min_length=1, max_length=100_000)
    shard_size: int = Field(default=16, ge=1, le=4096)
    min_tier: Tier = Tier.CPU_INT8


class ShardView(BaseModel):
    index: int
    state: ShardState
    device_id: str | None = None
    attempts: int = 0


class JobView(BaseModel):
    id: str
    model_ref: str
    status: JobStatus
    min_tier: Tier
    shard_count: int
    shards_done: int
    shards_failed: int
    shards_claimed: int
    cost_mc: int
    created_at: datetime
    completed_at: datetime | None = None
    shards: list[ShardView] = Field(default_factory=list)


class JobEvent(BaseModel):
    """One SSE frame on GET /jobs/{id}/events. Never carries payload data."""

    job_id: str
    type: Literal[
        "job.queued", "shard.claimed", "shard.done", "shard.failed",
        "shard.requeued", "job.completed", "job.failed", "heartbeat",
    ]
    index: int | None = None
    device_id: str | None = None
    shards_done: int = 0
    shards_failed: int = 0
    shard_count: int = 0
    ts: datetime


# --- devices ----------------------------------------------------------------

class DeviceRegister(BaseModel):
    pairing_code: str = Field(min_length=6, max_length=12)
    name: str = Field(min_length=1, max_length=64)
    capability: Capability


class DeviceHeartbeat(BaseModel):
    capability: Capability
    charging: bool
    wifi: bool
    screen_off: bool
    thermal_status: int = Field(ge=0, le=6)
    battery_pct: int = Field(ge=0, le=100)

    @property
    def eligible(self) -> bool:
        """The owner's device comes first. Everything must be true to compute."""
        from common.thermal import THERMAL_CEILING

        return (
            self.charging
            and self.wifi
            and self.screen_off
            and self.thermal_status <= THERMAL_CEILING
        )


class DeviceView(BaseModel):
    id: str
    name: str
    tier: Tier
    online: bool
    eligible: bool
    last_heartbeat_at: datetime | None
    shards_completed: int = 0
    earned_mc: int = 0


ShardResult.model_rebuild()
