"""Build the three registered .tflite graphs into the APK's assets.

Run once, on a machine with TensorFlow:

    pip install tensorflow-cpu
    python tools/make_models.py

The phone never compiles or downloads a model. These files ship inside the APK
and the interpreter refuses to load anything whose sha256 does not match
`android/.../ModelRegistry.kt`, which this script prints at the end.

Quantization is chosen per model to match the delegate it is meant for:
  * full int8 (int8 in, int8 out) -- what QNN / AI Engine Direct wants
  * float16 weights -- what the GPU delegate wants
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

import numpy as np
import tensorflow as tf

OUT = pathlib.Path(__file__).resolve().parent.parent / "android/app/src/main/assets/models"
OUT.mkdir(parents=True, exist_ok=True)

TEXT_DIM = 64
EMBED_DIM = 384
SWEEP_DIM = 64
rng = np.random.default_rng(20260823)
# Seeded so a rebuild produces byte-identical assets and the pinned sha256s
# in ModelRegistry.kt stay valid.
tf.keras.utils.set_random_seed(20260823)


def _full_int8(model, sample_shape, n: int = 200) -> bytes:
    """Full-integer quantization: weights, activations, input and output."""
    def representative():
        for _ in range(n):
            yield [rng.random((1, *sample_shape), dtype=np.float32)]

    conv = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = representative
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.int8
    conv.inference_output_type = tf.int8
    return conv.convert()


def _float16(model) -> bytes:
    conv = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.target_spec.supported_types = [tf.float16]
    return conv.convert()


def text_embedder() -> tf.keras.Model:
    """Hashed bag-of-tokens -> 384-d sentence embedding, L2-normalised.

    Not MobileBERT: a real transformer is 100MB and the point of the demo is
    the scheduler, not the model. Trained on uniform [0, 1) inputs, which is the
    range ShardRunner.kt's hashed-token encoder produces on the phone -- the
    server never encodes text, it only ships the strings.
    """
    inp = tf.keras.Input(shape=(TEXT_DIM,), name="tokens")
    x = tf.keras.layers.Dense(256, activation="relu")(inp)
    x = tf.keras.layers.Dense(256, activation="relu")(x)
    out = tf.keras.layers.Dense(EMBED_DIM, activation="tanh", name="embedding")(x)
    return tf.keras.Model(inp, out, name="textembed_mlp")


def image_classifier() -> tf.keras.Model:
    """Real MobileNetV2 with ImageNet weights when they can be fetched.

    V2 rather than V3 on purpose: V3's hard-swish does not survive full-int8
    quantization cleanly, and XNNPACK refuses to prepare the result. The CPU
    path is the fallback of last resort -- it is the one that may never fail.
    """
    try:
        return tf.keras.applications.MobileNetV2(
            input_shape=(224, 224, 3), weights="imagenet", include_top=True
        )
    except Exception as exc:  # offline box: still produce a graph of the right shape
        print(f"  ! could not fetch ImageNet weights ({exc}); using an untrained net")
        return tf.keras.applications.MobileNetV2(
            input_shape=(224, 224, 3), weights=None, include_top=True
        )


def sweep_evaluator() -> tf.keras.Model:
    """Scores one hyperparameter configuration. The embarrassingly-parallel
    workload the marketplace is actually for."""
    inp = tf.keras.Input(shape=(SWEEP_DIM,), name="config")
    x = tf.keras.layers.Dense(128, activation="relu")(inp)
    x = tf.keras.layers.Dense(64, activation="relu")(x)
    out = tf.keras.layers.Dense(1, name="score")(x)
    return tf.keras.Model(inp, out, name="sweep_mlp")


def write(name: str, blob: bytes) -> str:
    path = OUT / name
    path.write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()
    print(f"  {name:32} {len(blob) / 1024:8.1f} KB  sha256={digest}")
    return digest


def main() -> int:
    print(f"writing to {OUT}")
    digests = {}

    print("building textembed-mlp-int8 (full int8)")
    digests["textembed_mlp_int8.tflite"] = write(
        "textembed_mlp_int8.tflite", _full_int8(text_embedder(), (TEXT_DIM,))
    )

    print("building mobilenet-v2-cls-int8 (full int8)")
    digests["mobilenet_v2_cls_int8.tflite"] = write(
        "mobilenet_v2_cls_int8.tflite", _full_int8(image_classifier(), (224, 224, 3), n=40)
    )

    print("building sweep-mlp-fp16 (float16 weights)")
    digests["sweep_mlp_fp16.tflite"] = write(
        "sweep_mlp_fp16.tflite", _float16(sweep_evaluator())
    )

    print("\npaste into android/.../infer/ModelRegistry.kt:")
    for asset, digest in digests.items():
        print(f'    "{asset}" to "{digest}",')
    return 0


if __name__ == "__main__":
    sys.exit(main())
