package dev.neuromesh.provider

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * What the provider service is doing right now, for the UI and the
 * notification to read.
 *
 * A process-wide singleton rather than a bound service: the UI is a dashboard
 * over a loop that must keep running whether or not anything is looking at it,
 * and binder plumbing would buy nothing here.
 */
object ProviderState {

    data class Snapshot(
        val running: Boolean = false,
        val eligible: Boolean = false,
        val reason: String? = "Not started",
        val tierLabel: String = "unknown",
        val backend: String = "-",
        val currentShard: String? = null,
        val currentJob: String? = null,
        val shardsCompleted: Int = 0,
        val itemsProcessed: Int = 0,
        val earnedMc: Long = 0,
        val balanceMc: Long = 0,
        val lastDurationMs: Int = 0,
        val batteryPct: Int = 0,
        val thermalStatus: Int = 0,
        val lastError: String? = null,
        val log: List<String> = emptyList(),
    ) {
        val statusLine: String
            get() = when {
                !running -> "Stopped"
                currentShard != null -> "Computing shard $currentShard"
                eligible -> "Waiting for work"
                else -> reason ?: "Idle"
            }
    }

    private val _state = MutableStateFlow(Snapshot())
    val state: StateFlow<Snapshot> = _state.asStateFlow()

    fun update(block: (Snapshot) -> Snapshot) = _state.update(block)

    /** Newest first, capped -- a phone that runs all night must not grow a log
     *  that outlives the demo. */
    fun log(line: String) = _state.update {
        it.copy(log = (listOf(line) + it.log).take(MAX_LOG_LINES))
    }

    fun reset() = _state.update { Snapshot() }

    private const val MAX_LOG_LINES = 40
}
