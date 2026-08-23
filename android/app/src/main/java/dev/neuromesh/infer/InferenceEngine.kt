package dev.neuromesh.infer

import android.content.Context
import android.util.Log
import org.tensorflow.lite.Delegate
import org.tensorflow.lite.Interpreter
import org.tensorflow.lite.gpu.CompatibilityList
import org.tensorflow.lite.gpu.GpuDelegate
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest

/**
 * LiteRT, with a delegate ladder the demo cannot fall off.
 *
 * QNN (Qualcomm AI Engine Direct) first, LiteRT GPU delegate second, CPU last.
 * Explicitly *not* NNAPI: it is deprecated as of Android 15 and is not a path
 * worth building on.
 *
 * The ladder is the whole point. A slow demo beats a dead demo, so every rung
 * is tried in turn and a failure to initialise is logged and stepped over, not
 * thrown. The phone always ends up running the graph somehow.
 */
class InferenceEngine(private val context: Context) {

    enum class Backend(val wire: String) {
        QNN("qnn"), GPU("gpu"), CPU("cpu")
    }

    data class Session(
        val interpreter: Interpreter,
        val backend: Backend,
        val model: ModelRegistry.RegisteredModel,
        private val delegate: Delegate?,
    ) : AutoCloseable {
        override fun close() {
            interpreter.close()
            (delegate as? AutoCloseable)?.close()
        }
    }

    private val sessions = HashMap<String, Session>()

    @Synchronized
    fun session(ref: String): Session {
        sessions[ref]?.let { return it }
        val model = ModelRegistry[ref] ?: throw ModelRegistry.UnknownModel(ref)
        val graph = loadVerified(model)

        for (backend in Backend.entries) {
            val attempt = runCatching { build(graph, backend) }.getOrElse { e ->
                Log.w(TAG, "${backend.wire} delegate unavailable for $ref: ${e.message}")
                null
            }
            if (attempt != null) {
                Log.i(TAG, "loaded $ref on ${attempt.first.javaClass.simpleName} via ${backend.wire}")
                val session = Session(attempt.first, backend, model, attempt.second)
                sessions[ref] = session
                return session
            }
            // A delegate that half-initialised leaves the buffer position moved.
            graph.rewind()
        }
        error("no backend could load $ref -- not even CPU, which should be impossible")
    }

    private fun build(graph: ByteBuffer, backend: Backend): Pair<Interpreter, Delegate?> {
        val options = Interpreter.Options().apply {
            // One shard at a time, so the owner's phone keeps its other cores.
            numThreads = if (backend == Backend.CPU) CPU_THREADS else 1
        }
        val delegate = when (backend) {
            Backend.QNN -> QnnDelegates.create()
                ?: error("QNN runtime not present in this build")
            Backend.GPU -> {
                val compat = CompatibilityList()
                if (!compat.isDelegateSupportedOnThisDevice) error("GPU delegate unsupported here")
                GpuDelegate(compat.bestOptionsForThisDevice)
            }
            Backend.CPU -> null
        }
        delegate?.let { options.addDelegate(it) }
        return try {
            Interpreter(graph, options) to delegate
        } catch (e: Throwable) {
            (delegate as? AutoCloseable)?.close()
            throw e
        }
    }

    /**
     * Read the graph out of assets and refuse it unless it hashes to the digest
     * pinned in [ModelRegistry]. A tampered asset fails closed rather than
     * executing -- the registry is a security boundary, not a convenience.
     */
    private fun loadVerified(model: ModelRegistry.RegisteredModel): ByteBuffer {
        val bytes = context.assets.open(model.asset).use { it.readBytes() }
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
            .joinToString("") { "%02x".format(it) }
        if (digest != model.sha256) throw ModelRegistry.TamperedModel(model.asset)

        // The interpreter requires a direct buffer; a heap array will not do.
        return ByteBuffer.allocateDirect(bytes.size).order(ByteOrder.nativeOrder()).apply {
            put(bytes)
            rewind()
        }
    }

    /** Which rungs of the ladder this phone actually has, measured not assumed. */
    fun probe(): Capabilities {
        val gpu = runCatching { CompatibilityList().isDelegateSupportedOnThisDevice }
            .getOrDefault(false)
        val qnn = QnnDelegates.available()
        return Capabilities(qnnDelegate = qnn, gpuDelegate = gpu)
    }

    data class Capabilities(val qnnDelegate: Boolean, val gpuDelegate: Boolean)

    @Synchronized
    fun close() {
        sessions.values.forEach { runCatching { it.close() } }
        sessions.clear()
    }

    companion object {
        private const val TAG = "NeuroMesh/Infer"
        private const val CPU_THREADS = 4
    }
}

/**
 * Qualcomm's QNN delegate, loaded reflectively.
 *
 * The AI Engine Direct AAR is not on a public Maven repository, so it cannot be
 * a compile-time dependency of a build that has to work on any machine. Drop
 * the Qualcomm AAR into `app/libs/` and this lights up; without it the engine
 * steps down to the GPU delegate and the demo still runs.
 */
private object QnnDelegates {

    private const val TAG = "NeuroMesh/QNN"
    private const val DELEGATE_CLASS = "com.qualcomm.qti.QnnDelegate"
    private const val OPTIONS_CLASS = "com.qualcomm.qti.QnnDelegate\$Options"

    fun available(): Boolean = runCatching { Class.forName(DELEGATE_CLASS) }.isSuccess

    fun create(): Delegate? = runCatching {
        val delegateClass = Class.forName(DELEGATE_CLASS)
        val optionsClass = Class.forName(OPTIONS_CLASS)
        val options = optionsClass.getDeclaredConstructor().newInstance()

        // Ask for the HTP (the NPU) rather than letting it settle for the DSP.
        runCatching {
            val backendField = optionsClass.getMethod("setBackendType", Int::class.javaPrimitiveType)
            backendField.invoke(options, HTP_BACKEND)
        }.onFailure { Log.i(TAG, "QNN backend selector absent; using its default") }

        delegateClass.getConstructor(optionsClass).newInstance(options) as Delegate
    }.onFailure { Log.i(TAG, "QNN delegate not present: ${it.message}") }.getOrNull()

    private const val HTP_BACKEND = 2
}
