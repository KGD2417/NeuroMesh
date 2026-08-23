package dev.neuromesh.provider

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.util.Log
import dev.neuromesh.MainActivity
import dev.neuromesh.NeuroMeshApp
import dev.neuromesh.R
import dev.neuromesh.data.Session
import dev.neuromesh.infer.InferenceEngine
import dev.neuromesh.infer.ModelRegistry
import dev.neuromesh.infer.ShardRunner
import dev.neuromesh.net.Api
import dev.neuromesh.net.DeviceLog
import dev.neuromesh.net.HeartbeatRequest
import dev.neuromesh.net.ShardAssignment
import dev.neuromesh.net.ShardFailure
import dev.neuromesh.net.ShardResult
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.Instant
import kotlin.system.measureTimeMillis

/**
 * The provider loop, as a foreground service.
 *
 * Heartbeat, claim, compute, settle -- and stop the instant the phone stops
 * being eligible. A foreground service because this is exactly the thing
 * Android's background limits exist to kill: it runs for hours, on the network,
 * with the screen off. The notification is not a formality, it is the owner's
 * off switch.
 */
class ProviderService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private lateinit var session: Session
    private lateinit var api: Api
    private lateinit var engine: InferenceEngine
    private lateinit var eligibility: Eligibility
    private var loop: Job? = null
    private var leaseTtlS = 30

    override fun onCreate() {
        super.onCreate()
        val app = application as NeuroMeshApp
        session = app.session
        api = app.api
        engine = app.engine
        eligibility = Eligibility(this, engine)
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIFICATION_ID, notification(ProviderState.state.value))
        if (loop?.isActive != true) {
            ProviderState.update { it.copy(running = true, reason = "Starting up") }
            loop = scope.launch { run() }
        }
        // START_STICKY: if Android kills us for memory, come back. The lease
        // reaper on the server has already handed our shard to someone else by
        // then, which is exactly why leases exist.
        return START_STICKY
    }

    override fun onDestroy() {
        loop?.cancel()
        scope.cancel()
        engine.close()
        ProviderState.update { it.copy(running = false, currentShard = null, reason = "Stopped") }
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // --- the loop -----------------------------------------------------------

    private suspend fun run() {
        var idleBackoffMs = MIN_IDLE_BACKOFF_MS
        var sinceHeartbeat = Long.MAX_VALUE

        while (scope.isActive) {
            try {
                val state = eligibility.state()

                if (sinceHeartbeat >= HEARTBEAT_INTERVAL_MS) {
                    heartbeat(state)
                    sinceHeartbeat = 0
                }

                if (!state.eligible) {
                    ProviderState.update {
                        it.copy(
                            eligible = false,
                            reason = state.reason,
                            currentShard = null,
                            batteryPct = state.batteryPct,
                            thermalStatus = state.thermalStatus,
                        )
                    }
                    notify(ProviderState.state.value)
                    delay(INELIGIBLE_POLL_MS)
                    sinceHeartbeat += INELIGIBLE_POLL_MS
                    continue
                }

                val assignment = api.claim()
                if (assignment == null) {
                    // Empty queue is the normal case. Back off rather than spin:
                    // a phone polling every 200ms all night is a battery bug.
                    ProviderState.update { it.copy(eligible = true, reason = null, currentShard = null) }
                    notify(ProviderState.state.value)
                    delay(idleBackoffMs)
                    sinceHeartbeat += idleBackoffMs
                    idleBackoffMs = (idleBackoffMs * 2).coerceAtMost(MAX_IDLE_BACKOFF_MS)
                    continue
                }

                idleBackoffMs = MIN_IDLE_BACKOFF_MS
                execute(assignment, state)
                sinceHeartbeat += 1_000
            } catch (e: Exception) {
                if (!scope.isActive) return
                Log.w(TAG, "loop error: ${e.message}")
                ProviderState.update { it.copy(lastError = e.message, currentShard = null) }
                ProviderState.log("error: ${e.message?.take(120)}")
                notify(ProviderState.state.value)
                delay(ERROR_BACKOFF_MS)
                sinceHeartbeat += ERROR_BACKOFF_MS
            }
        }
    }

    private suspend fun heartbeat(state: Eligibility.State) {
        val response = api.heartbeat(
            HeartbeatRequest(
                capability = eligibility.capability(),
                charging = state.charging,
                wifi = state.wifi,
                screenOff = state.screenOff,
                thermalStatus = state.thermalStatus,
                batteryPct = state.batteryPct,
            )
        )
        leaseTtlS = response.leaseTtlS
        ProviderState.update {
            it.copy(
                tierLabel = response.tierLabel,
                eligible = response.eligible,
                reason = state.reason ?: response.reason,
                batteryPct = state.batteryPct,
                thermalStatus = state.thermalStatus,
            )
        }
    }

    /**
     * Run one shard, renewing the lease while it runs.
     *
     * If the renewal fails the shard is abandoned mid-flight: the server has
     * already given it to another phone, and finishing it would only produce a
     * result nobody will accept.
     */
    private suspend fun execute(assignment: ShardAssignment, state: Eligibility.State) {
        ProviderState.update {
            it.copy(
                currentShard = "#${assignment.index}",
                currentJob = assignment.jobId.take(8),
                reason = null,
            )
        }
        notify(ProviderState.state.value)

        val renewer = scope.launch {
            // A third of the lease: two renewals may fail before we are at risk.
            val interval = (leaseTtlS * 1000L / 3).coerceAtLeast(2_000L)
            while (isActive) {
                delay(interval)
                val renewed = runCatching { api.renewLease(assignment.shardId) }.isSuccess
                if (!renewed) {
                    Log.w(TAG, "lost lease on shard ${assignment.shardId}")
                    ProviderState.log("lost lease on shard #${assignment.index}")
                    break
                }
            }
        }

        try {
            val graph = engine.session(assignment.modelRef)
            var outputs: List<kotlinx.serialization.json.JsonElement> = emptyList()
            val elapsed = measureTimeMillis {
                outputs = withContext(Dispatchers.Default) {
                    ShardRunner.run(graph, assignment.items)
                }
            }
            renewer.cancel()

            val ack = api.completeShard(
                assignment.shardId,
                ShardResult(
                    outputs = outputs,
                    durationMs = elapsed.toInt(),
                    delegate = graph.backend.wire,
                    deviceLogs = listOf(
                        DeviceLog(
                            ts = Instant.now().toString(),
                            level = "info",
                            event = "shard.done",
                            detail = "${assignment.items.size} items on ${graph.backend.wire}",
                            thermalStatus = state.thermalStatus,
                            batteryPct = state.batteryPct,
                        )
                    ),
                ),
            )

            if (ack.accepted) {
                ProviderState.update {
                    it.copy(
                        shardsCompleted = it.shardsCompleted + 1,
                        itemsProcessed = it.itemsProcessed + assignment.items.size,
                        earnedMc = it.earnedMc + ack.payoutMc,
                        balanceMc = ack.balanceMc,
                        lastDurationMs = elapsed.toInt(),
                        backend = graph.backend.wire,
                        currentShard = null,
                        lastError = null,
                    )
                }
                ProviderState.log(
                    "shard #${assignment.index} · ${assignment.items.size} items · " +
                        "${elapsed}ms · ${graph.backend.wire} · +${ack.payoutMc} mC"
                )
            } else {
                ProviderState.update { it.copy(currentShard = null) }
                ProviderState.log("shard #${assignment.index} rejected: ${ack.detail}")
            }
        } catch (e: ModelRegistry.UnknownModel) {
            // Not retryable anywhere in the fleet: no build has this graph.
            reportFailure(assignment, e.message ?: "unknown model", retryable = false)
        } catch (e: ModelRegistry.TamperedModel) {
            reportFailure(assignment, e.message ?: "tampered model", retryable = false)
        } catch (e: ShardRunner.BadInput) {
            reportFailure(assignment, e.message ?: "bad shard input", retryable = false)
        } catch (e: Exception) {
            // Anything else -- OOM, delegate crash, network -- is another
            // phone's problem to retry, not a reason to burn the shard.
            reportFailure(assignment, e.message ?: e::class.java.simpleName, retryable = true)
        } finally {
            renewer.cancel()
            notify(ProviderState.state.value)
        }
    }

    private suspend fun reportFailure(
        assignment: ShardAssignment, reason: String, retryable: Boolean,
    ) {
        Log.w(TAG, "shard ${assignment.shardId} failed: $reason")
        ProviderState.update { it.copy(currentShard = null, lastError = reason) }
        ProviderState.log("shard #${assignment.index} failed: ${reason.take(100)}")
        runCatching {
            api.failShard(assignment.shardId, ShardFailure(reason.take(500), retryable))
        }
    }

    // --- notification -------------------------------------------------------

    private fun createChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.provider_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.provider_channel_description)
            setShowBadge(false)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun notification(snapshot: ProviderState.Snapshot): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, ProviderService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle(snapshot.statusLine)
            .setContentText(
                "${snapshot.shardsCompleted} shards · ${snapshot.earnedMc} mC earned · " +
                    snapshot.tierLabel
            )
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentIntent(open)
            .addAction(
                Notification.Action.Builder(null as android.graphics.drawable.Icon?, "Stop", stop)
                    .build()
            )
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    private fun notify(snapshot: ProviderState.Snapshot) {
        runCatching {
            getSystemService(NotificationManager::class.java)
                .notify(NOTIFICATION_ID, notification(snapshot))
        }
    }

    companion object {
        private const val TAG = "NeuroMesh/Provider"
        private const val CHANNEL_ID = "provider"
        private const val NOTIFICATION_ID = 1
        const val ACTION_STOP = "dev.neuromesh.STOP_PROVIDING"

        private const val HEARTBEAT_INTERVAL_MS = 20_000L
        private const val INELIGIBLE_POLL_MS = 5_000L
        private const val MIN_IDLE_BACKOFF_MS = 1_500L
        private const val MAX_IDLE_BACKOFF_MS = 15_000L
        private const val ERROR_BACKOFF_MS = 5_000L

        fun start(context: Context) {
            val intent = Intent(context, ProviderService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, ProviderService::class.java))
        }
    }
}
