package dev.neuromesh.infer

import android.util.Base64
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonPrimitive
import org.tensorflow.lite.DataType
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.exp
import kotlin.math.roundToInt

/**
 * Turns one shard into one list of outputs.
 *
 * The shard's items arrive as JSON, are encoded into the exact tensor the
 * registered graph declares, run, and decoded back to JSON. Outputs come back
 * in the same order as the items -- the aggregator assembles by shard index,
 * and within a shard by position, so a reordering here would silently corrupt
 * a result rather than fail it.
 */
object ShardRunner {

    /** Item counts above this are refused: a shard should be seconds, not minutes. */
    const val MAX_ITEMS_PER_SHARD = 4096

    class BadInput(message: String) : IllegalArgumentException(message)

    fun run(session: InferenceEngine.Session, items: List<JsonElement>): List<JsonElement> {
        require(items.size <= MAX_ITEMS_PER_SHARD) {
            "shard has ${items.size} items, over the $MAX_ITEMS_PER_SHARD ceiling"
        }
        val interpreter = session.interpreter
        val inputTensor = interpreter.getInputTensor(0)
        val outputTensor = interpreter.getOutputTensor(0)

        val inScale = inputTensor.quantizationParams().scale
        val inZero = inputTensor.quantizationParams().zeroPoint
        val outScale = outputTensor.quantizationParams().scale
        val outZero = outputTensor.quantizationParams().zeroPoint

        val input = direct(inputTensor.numBytes())
        val output = direct(outputTensor.numBytes())

        return items.map { item ->
            val features = encode(session.model, item)
            input.rewind()
            writeTensor(input, inputTensor.dataType(), features, inScale, inZero)
            input.rewind()
            output.rewind()

            interpreter.run(input, output)

            output.rewind()
            val values = readTensor(
                output, outputTensor.dataType(), session.model.outputDim, outScale, outZero
            )
            decode(session.model, values)
        }
    }

    // --- input encoding -----------------------------------------------------

    private fun encode(model: ModelRegistry.RegisteredModel, item: JsonElement): FloatArray =
        when (model.inputKind) {
            ModelRegistry.InputKind.TEXT -> encodeText(text(item), model.inputDim)
            ModelRegistry.InputKind.FLOAT_VECTOR -> encodeVector(item, model.inputDim)
            ModelRegistry.InputKind.IMAGE_RGB -> encodeImage(text(item), model.inputDim)
        }

    /**
     * Hashed bag-of-tokens, scaled into [0, 1] -- the range the graph was
     * quantized against. Not a real tokenizer, and does not pretend to be one:
     * the marketplace is the contribution here, not the embedding model.
     */
    private fun encodeText(value: String, dim: Int): FloatArray {
        val features = FloatArray(dim)
        val tokens = value.lowercase().split(TOKEN_SPLIT).filter { it.isNotBlank() }
        if (tokens.isEmpty()) return features
        for (token in tokens) {
            val bucket = ((token.hashCode() % dim) + dim) % dim
            features[bucket] += 1f
        }
        val scale = tokens.size.toFloat()
        for (i in features.indices) features[i] = (features[i] / scale).coerceIn(0f, 1f)
        return features
    }

    private fun encodeVector(item: JsonElement, dim: Int): FloatArray {
        val array = (item as? JsonArray)
            ?: throw BadInput("expected an array of numbers, got ${item::class.simpleName}")
        val features = FloatArray(dim)
        for (i in 0 until minOf(dim, array.size)) {
            features[i] = array[i].jsonPrimitive.doubleOrNull?.toFloat()
                ?: throw BadInput("element $i is not a number")
        }
        return features
    }

    /** base64 of exactly width*height*3 bytes, RGB, row-major. */
    private fun encodeImage(value: String, dim: Int): FloatArray {
        val bytes = try {
            Base64.decode(value, Base64.DEFAULT)
        } catch (e: IllegalArgumentException) {
            throw BadInput("image item is not valid base64: ${e.message}")
        }
        if (bytes.size != dim) throw BadInput("image is ${bytes.size} bytes, expected $dim")
        return FloatArray(dim) { (bytes[it].toInt() and 0xFF) / 255f }
    }

    // --- tensor plumbing ----------------------------------------------------

    private fun direct(bytes: Int): ByteBuffer =
        ByteBuffer.allocateDirect(bytes).order(ByteOrder.nativeOrder())

    private fun writeTensor(
        buffer: ByteBuffer, type: DataType, values: FloatArray, scale: Float, zeroPoint: Int,
    ) {
        when (type) {
            DataType.FLOAT32 -> values.forEach { buffer.putFloat(it) }
            DataType.INT8 -> values.forEach { buffer.put(quantize(it, scale, zeroPoint)) }
            DataType.UINT8 -> values.forEach {
                buffer.put(((it / scale).roundToInt() + zeroPoint).coerceIn(0, 255).toByte())
            }
            else -> throw BadInput("unsupported input tensor type $type")
        }
    }

    private fun readTensor(
        buffer: ByteBuffer, type: DataType, count: Int, scale: Float, zeroPoint: Int,
    ): FloatArray = when (type) {
        DataType.FLOAT32 -> FloatArray(count) { buffer.float }
        DataType.INT8 -> FloatArray(count) { scale * (buffer.get().toInt() - zeroPoint) }
        DataType.UINT8 -> FloatArray(count) {
            scale * ((buffer.get().toInt() and 0xFF) - zeroPoint)
        }
        else -> throw BadInput("unsupported output tensor type $type")
    }

    private fun quantize(value: Float, scale: Float, zeroPoint: Int): Byte =
        ((value / scale).roundToInt() + zeroPoint).coerceIn(-128, 127).toByte()

    // --- output decoding ----------------------------------------------------

    private fun decode(
        model: ModelRegistry.RegisteredModel, values: FloatArray,
    ): JsonElement = when (model.inputKind) {
        // Full embedding, rounded: 384 float32 per item is most of the uplink
        // budget on a phone, and four decimals is well past what a cosine
        // similarity can tell apart.
        ModelRegistry.InputKind.TEXT -> buildJsonArray {
            values.forEach { add(JsonPrimitive(round4(it))) }
        }
        // Top-5 rather than 1000 logits, for the same reason.
        ModelRegistry.InputKind.IMAGE_RGB -> buildJsonObject {
            put("top", topK(values, 5))
        }
        ModelRegistry.InputKind.FLOAT_VECTOR -> JsonPrimitive(round4(values.firstOrNull() ?: 0f))
    }

    private fun topK(logits: FloatArray, k: Int): JsonArray {
        val max = logits.max()
        val expSum = logits.sumOf { exp((it - max).toDouble()) }
        val order = logits.indices.sortedByDescending { logits[it] }.take(k)
        return buildJsonArray {
            order.forEach { index ->
                add(
                    buildJsonObject {
                        put("class", JsonPrimitive(index))
                        put("score", JsonPrimitive(round4((exp((logits[index] - max).toDouble()) / expSum).toFloat())))
                    }
                )
            }
        }
    }

    private fun round4(value: Float): Double = Math.round(value * 10_000.0) / 10_000.0

    private fun text(item: JsonElement): String = (item as? JsonPrimitive)?.content
        ?: throw BadInput("expected a string item, got ${item::class.simpleName}")

    private val TOKEN_SPLIT = Regex("[^\\p{L}\\p{N}]+")
}
