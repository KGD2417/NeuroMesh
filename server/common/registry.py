"""The model allow-list.

The phone never runs arbitrary code. It runs one of these graphs, in a fixed
LiteRT interpreter, and nothing else. This is a security property first -- it
is also why NeuroMesh cannot accept a user-uploaded model or a Python function,
and that limitation is deliberate rather than unfinished.

`sha256` is what the phone verifies the bundled .tflite against before it will
load it, so a tampered asset fails closed instead of executing.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.tiers import Tier


@dataclass(frozen=True, slots=True)
class RegisteredModel:
    ref: str
    description: str
    asset: str              # filename in the APK's assets/models/
    sha256: str             # the phone verifies the asset against this
    min_tier: Tier
    input_kind: str         # "text" | "float_vector" | "image_rgb"
    input_dim: int | None   # fixed input width where the graph demands one
    output_dim: int
    quantization: str


REGISTRY: dict[str, RegisteredModel] = {
    m.ref: m
    for m in (
        RegisteredModel(
            ref="textembed-mlp-int8",
            description="Sentence embeddings, 384-d, full-int8 quantized",
            asset="textembed_mlp_int8.tflite",
            sha256="662e8d1a1a42ea58a635d018585c12023577e0bdea2259c62aee3c2974c580a9",
            min_tier=Tier.CPU_INT8,
            input_kind="text",
            input_dim=64,
            output_dim=384,
            quantization="int8",
        ),
        RegisteredModel(
            ref="mobilenet-v2-cls-int8",
            description="ImageNet-1k classification, 224x224 RGB, full-int8",
            asset="mobilenet_v2_cls_int8.tflite",
            sha256="8619f3d3a59bdaeb87fbca7d51eb448ec1bba4e4ca8a98aef5ed0bae9b5f722f",
            min_tier=Tier.CPU_INT8,
            input_kind="image_rgb",
            input_dim=224 * 224 * 3,
            output_dim=1000,
            quantization="int8",
        ),
        RegisteredModel(
            ref="sweep-mlp-fp16",
            description="Hyperparameter sweep evaluator, fp16 (NPU path demo)",
            asset="sweep_mlp_fp16.tflite",
            sha256="17f6a5b8d3f40b5804ae2a5e38fd0431128300fe521fe04c3f98dd02c830a41a",
            min_tier=Tier.GPU_FP16,
            input_kind="float_vector",
            input_dim=64,
            output_dim=1,
            quantization="fp16",
        ),
    )
}


def get(ref: str) -> RegisteredModel | None:
    return REGISTRY.get(ref)


def refs() -> list[str]:
    return sorted(REGISTRY)
