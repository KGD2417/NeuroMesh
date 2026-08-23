package dev.neuromesh.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import dev.neuromesh.NeuroMeshApp
import dev.neuromesh.data.Session
import dev.neuromesh.net.Account
import dev.neuromesh.net.DeviceRegister
import dev.neuromesh.net.DeviceView
import dev.neuromesh.net.EventStream
import dev.neuromesh.net.JobEvent
import dev.neuromesh.net.JobResult
import dev.neuromesh.net.JobSubmit
import dev.neuromesh.net.JobView
import dev.neuromesh.net.ModelInfo
import dev.neuromesh.provider.Eligibility
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlin.random.Random

/** Every screen's state, in one place. */
class AppViewModel(app: Application) : AndroidViewModel(app) {

    enum class Screen { SETUP, MODE, PROVIDER, CONSUMER, JOB }

    data class UiState(
        val screen: Screen = Screen.SETUP,
        val busy: Boolean = false,
        val error: String? = null,
        val notice: String? = null,
        val orchestrator: String = "",
        val email: String = "",
        val account: Account? = null,
        val devices: List<DeviceView> = emptyList(),
        val models: List<ModelInfo> = emptyList(),
        val pairingCode: String? = null,
        val paired: Boolean = false,
        val deviceTier: String = "",
        // consumer
        val jobs: List<JobView> = emptyList(),
        val activeJob: JobView? = null,
        val liveEvents: List<JobEvent> = emptyList(),
        val result: JobResult? = null,
    )

    private val appCtx = app as NeuroMeshApp
    private val api = appCtx.api
    val session: Session = appCtx.session
    val eligibility = Eligibility(app, appCtx.engine)

    private val _ui = MutableStateFlow(
        UiState(
            orchestrator = session.orchestrator(),
            email = session.email().orEmpty(),
            paired = session.isPaired(),
            screen = startScreen(),
        )
    )
    val ui: StateFlow<UiState> = _ui.asStateFlow()

    private var eventJob: Job? = null

    private fun startScreen(): Screen = when {
        !session.isLoggedIn() -> Screen.SETUP
        session.mode() == Session.Mode.PROVIDER -> Screen.PROVIDER
        session.mode() == Session.Mode.CONSUMER -> Screen.CONSUMER
        else -> Screen.MODE
    }

    // --- plumbing -----------------------------------------------------------

    private fun run(block: suspend () -> Unit) {
        viewModelScope.launch {
            _ui.update { it.copy(busy = true, error = null) }
            try {
                block()
            } catch (e: Exception) {
                _ui.update { it.copy(error = e.message ?: e::class.java.simpleName) }
            } finally {
                _ui.update { it.copy(busy = false) }
            }
        }
    }

    fun dismissError() = _ui.update { it.copy(error = null, notice = null) }

    fun setOrchestrator(url: String) {
        session.setOrchestrator(url)
        _ui.update { it.copy(orchestrator = session.orchestrator()) }
    }

    fun goTo(screen: Screen) = _ui.update { it.copy(screen = screen) }

    // --- auth ---------------------------------------------------------------

    fun login(email: String, password: String, register: Boolean) = run {
        val tokens = if (register) api.register(email, password) else api.login(email, password)
        session.saveTokens(tokens.accessToken, tokens.refreshToken, tokens.userId, email)
        _ui.update { it.copy(email = email, screen = Screen.MODE) }
        refreshAccount()
    }

    fun chooseMode(mode: Session.Mode) {
        session.setMode(mode)
        _ui.update {
            it.copy(screen = if (mode == Session.Mode.PROVIDER) Screen.PROVIDER else Screen.CONSUMER)
        }
        if (mode == Session.Mode.CONSUMER) refreshConsumer() else refreshAccount()
    }

    fun logout() {
        session.logout()
        _ui.value = UiState(orchestrator = session.orchestrator())
    }

    // --- shared -------------------------------------------------------------

    fun refreshAccount() = run {
        val account = api.account()
        val devices = api.myDevices()
        _ui.update { it.copy(account = account, devices = devices, paired = session.isPaired()) }
    }

    // --- provider -----------------------------------------------------------

    /** Owner mints a code on the consumer phone; the provider phone redeems it. */
    fun mintPairingCode() = run {
        val code = api.pairingCode()
        _ui.update { it.copy(pairingCode = code.code) }
    }

    fun pairThisPhone(code: String, name: String) = run {
        val credentials = api.registerDevice(
            DeviceRegister(
                pairingCode = code.trim().uppercase(),
                name = name.ifBlank { android.os.Build.MODEL },
                capability = eligibility.capability(),
            )
        )
        session.saveDevice(credentials.deviceId, credentials.deviceKey, name)
        _ui.update {
            it.copy(
                paired = true,
                deviceTier = credentials.tierLabel,
                pairingCode = null,
                notice = "Paired as ${credentials.tierLabel}",
            )
        }
    }

    fun unpair() {
        session.clearDevice()
        _ui.update { it.copy(paired = false, notice = "This phone left the fleet") }
    }

    // --- consumer -----------------------------------------------------------

    fun refreshConsumer() = run {
        val models = api.models()
        val account = api.account()
        _ui.update { it.copy(models = models, account = account) }
    }

    fun submitJob(modelRef: String, itemCount: Int, shardSize: Int) = run {
        val model = _ui.value.models.firstOrNull { it.ref == modelRef }
        val inputs = syntheticInputs(model?.inputKind ?: "text", itemCount)
        val job = api.submitJob(JobSubmit(modelRef, inputs, shardSize))
        _ui.update {
            it.copy(activeJob = job, screen = Screen.JOB, liveEvents = emptyList(), result = null)
        }
        watch(job.id)
    }

    /**
     * Live shard progress. The SSE stream is the display; the poll on each
     * event is what keeps the shard grid honest if a frame is ever missed.
     */
    private fun watch(jobId: String) {
        eventJob?.cancel()
        eventJob = viewModelScope.launch {
            EventStream.jobEvents(api, jobId).collect { event ->
                _ui.update { it.copy(liveEvents = (it.liveEvents + event).takeLast(60)) }
                runCatching { api.job(jobId) }.getOrNull()?.let { job ->
                    _ui.update { it.copy(activeJob = job) }
                }
                if (event.type == "job.completed" || event.type == "job.failed") {
                    runCatching { api.jobResult(jobId) }.getOrNull()?.let { result ->
                        _ui.update { it.copy(result = result) }
                    }
                    runCatching { api.account() }.getOrNull()?.let { account ->
                        _ui.update { it.copy(account = account) }
                    }
                }
            }
        }
    }

    fun refreshJob() = run {
        val id = _ui.value.activeJob?.id ?: return@run
        _ui.update { it.copy(activeJob = api.job(id)) }
    }

    fun cancelJob() = run {
        val id = _ui.value.activeJob?.id ?: return@run
        eventJob?.cancel()
        _ui.update { it.copy(activeJob = api.cancelJob(id), notice = "Job cancelled, escrow refunded") }
    }

    override fun onCleared() {
        eventJob?.cancel()
        super.onCleared()
    }

    /**
     * The demo submits generated inputs rather than asking someone to type a
     * thousand sentences into a phone. Shape matches what the registered graph
     * expects, which is all the orchestrator ever checks.
     */
    private fun syntheticInputs(inputKind: String, count: Int): List<JsonElement> {
        val random = Random(count.toLong())
        return when (inputKind) {
            "float_vector" -> List(count) {
                buildJsonArray { repeat(64) { add(JsonPrimitive(random.nextDouble())) } }
            }
            "image_rgb" -> List(count) {
                // 224*224*3 zero bytes, base64. Enough to exercise the graph and
                // the uplink without shipping a photo library in the APK.
                JsonPrimitive(
                    android.util.Base64.encodeToString(
                        ByteArray(224 * 224 * 3), android.util.Base64.NO_WRAP
                    )
                )
            }
            else -> List(count) { JsonPrimitive(SENTENCES[it % SENTENCES.size] + " #$it") }
        }
    }

    private companion object {
        val SENTENCES = listOf(
            "the phone in your pocket has a neural processor",
            "idle silicon is the cheapest compute there is",
            "forty tops, plugged in, doing nothing all night",
            "a fleet of phones is a datacenter nobody built",
            "inference only, never training, by design",
            "shards are independent or they are not shards",
        )
    }
}
