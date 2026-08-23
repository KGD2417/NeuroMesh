package dev.neuromesh.infer

/**
 * The allow-list, mirrored from server/common/registry.py.
 *
 * The phone never runs arbitrary code. It runs one of these graphs, from its
 * own assets, in a fixed interpreter. A shard names a model_ref; if the ref is
 * not in this map the shard is failed rather than improvised, and if the asset
 * on disk does not hash to [sha256] it is refused rather than loaded.
 *
 * Regenerate the assets with `python tools/make_models.py`, which prints the
 * digests to paste here.
 */
object ModelRegistry {

    enum class InputKind { TEXT, FLOAT_VECTOR, IMAGE_RGB }

    data class RegisteredModel(
        val ref: String,
        val asset: String,
        val sha256: String,
        val inputKind: InputKind,
        val inputDim: Int,
        val outputDim: Int,
        val quantization: String,
    )

    private val models = listOf(
        RegisteredModel(
            ref = "textembed-mlp-int8",
            asset = "models/textembed_mlp_int8.tflite",
            sha256 = "662e8d1a1a42ea58a635d018585c12023577e0bdea2259c62aee3c2974c580a9",
            inputKind = InputKind.TEXT,
            inputDim = 64,
            outputDim = 384,
            quantization = "int8",
        ),
        RegisteredModel(
            ref = "mobilenet-v2-cls-int8",
            asset = "models/mobilenet_v2_cls_int8.tflite",
            sha256 = "8619f3d3a59bdaeb87fbca7d51eb448ec1bba4e4ca8a98aef5ed0bae9b5f722f",
            inputKind = InputKind.IMAGE_RGB,
            inputDim = 224 * 224 * 3,
            outputDim = 1000,
            quantization = "int8",
        ),
        RegisteredModel(
            ref = "sweep-mlp-fp16",
            asset = "models/sweep_mlp_fp16.tflite",
            sha256 = "17f6a5b8d3f40b5804ae2a5e38fd0431128300fe521fe04c3f98dd02c830a41a",
            inputKind = InputKind.FLOAT_VECTOR,
            inputDim = 64,
            outputDim = 1,
            quantization = "fp16",
        ),
    ).associateBy { it.ref }

    operator fun get(ref: String): RegisteredModel? = models[ref]

    fun refs(): List<String> = models.keys.sorted()

    /** Every graph this build can run -- what the phone advertises at pairing. */
    fun quantizations(): List<String> = models.values.map { it.quantization }.distinct()

    class UnknownModel(ref: String) :
        IllegalArgumentException("model '$ref' is not registered in this build")

    class TamperedModel(asset: String) :
        IllegalStateException("asset '$asset' does not match its pinned sha256")
}
