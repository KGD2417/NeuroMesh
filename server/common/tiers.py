"""Capability tiers.

Tiers are derived from what a device can *measure* about itself -- available
RAM, which LiteRT delegate actually initialised, which quantizations the
delegate accepts -- never from a marketing name. A job declares the minimum
tier it needs; a device serves any shard at or below its own tier.
"""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, Field


class Tier(IntEnum):
    """Ordered weakest -> strongest. The integer value is the wire format."""

    CPU_INT8 = 0    # no delegate came up; XNNPACK/CPU only
    GPU_FP16 = 1    # LiteRT GPU delegate (the fallback path that must always work)
    NPU_INT8 = 2    # QNN / AI Engine Direct, int8 graphs
    NPU_FP16 = 3    # QNN with fp16 support and headroom for larger graphs

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    Tier.CPU_INT8: "CPU int8",
    Tier.GPU_FP16: "GPU fp16",
    Tier.NPU_INT8: "NPU int8",
    Tier.NPU_FP16: "NPU fp16",
}

# RAM floors, in MB of *available* (not total) memory.
_GPU_MIN_RAM_MB = 1200
_NPU_MIN_RAM_MB = 1800
_NPU_FP16_MIN_RAM_MB = 2600


class Capability(BaseModel):
    """What a phone reports about itself at registration and on heartbeat."""

    available_ram_mb: int = Field(ge=0)
    qnn_delegate: bool = False
    gpu_delegate: bool = False
    quantizations: list[str] = Field(default_factory=lambda: ["int8"])
    soc: str | None = None
    npu_tops: float | None = None


def derive_tier(cap: Capability) -> Tier:
    """Measured capability -> tier. Conservative: a claim we cannot verify loses."""
    q = {s.lower() for s in cap.quantizations}
    if cap.qnn_delegate and "fp16" in q and cap.available_ram_mb >= _NPU_FP16_MIN_RAM_MB:
        return Tier.NPU_FP16
    if cap.qnn_delegate and "int8" in q and cap.available_ram_mb >= _NPU_MIN_RAM_MB:
        return Tier.NPU_INT8
    if cap.gpu_delegate and cap.available_ram_mb >= _GPU_MIN_RAM_MB:
        return Tier.GPU_FP16
    return Tier.CPU_INT8


def tiers_servable_by(tier: Tier) -> list[Tier]:
    """Tiers this device may claim from, strongest first.

    Strongest-first is deliberate: a capable phone should drain the work that
    only capable phones can do before it takes work anything could have run.
    """
    return sorted((t for t in Tier if t <= tier), reverse=True)


def parse_tier(value: int | str) -> Tier:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, str):
        return Tier[value.upper()]
    return Tier(value)
