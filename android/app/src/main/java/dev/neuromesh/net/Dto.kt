package dev.neuromesh.net

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * Wire types, mirroring server/common/schemas.py.
 *
 * Kept hand-written rather than generated: there are a dozen of them, and a
 * generator is one more thing to fix at 3am.
 */

@Serializable
data class Credentials(val email: String, val password: String)

@Serializable
data class TokenPair(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("user_id") val userId: String,
)

@Serializable
data class RefreshRequest(@SerialName("refresh_token") val refreshToken: String)

// --- capability + heartbeat -------------------------------------------------

@Serializable
data class Capability(
    @SerialName("available_ram_mb") val availableRamMb: Int,
    @SerialName("qnn_delegate") val qnnDelegate: Boolean,
    @SerialName("gpu_delegate") val gpuDelegate: Boolean,
    val quantizations: List<String>,
    val soc: String? = null,
    @SerialName("npu_tops") val npuTops: Float? = null,
)

@Serializable
data class HeartbeatRequest(
    val capability: Capability,
    val charging: Boolean,
    val wifi: Boolean,
    @SerialName("screen_off") val screenOff: Boolean,
    @SerialName("thermal_status") val thermalStatus: Int,
    @SerialName("battery_pct") val batteryPct: Int,
)

@Serializable
data class HeartbeatResponse(
    val tier: Int,
    @SerialName("tier_label") val tierLabel: String,
    val eligible: Boolean,
    @SerialName("may_claim") val mayClaim: Boolean,
    @SerialName("lease_ttl_s") val leaseTtlS: Int,
    val reason: String? = null,
)

// --- pairing ----------------------------------------------------------------

@Serializable
data class PairingCode(val code: String, @SerialName("expires_in_s") val expiresInS: Int)

@Serializable
data class DeviceRegister(
    @SerialName("pairing_code") val pairingCode: String,
    val name: String,
    val capability: Capability,
)

@Serializable
data class DeviceCredentials(
    @SerialName("device_id") val deviceId: String,
    @SerialName("device_key") val deviceKey: String,
    val tier: Int,
    @SerialName("tier_label") val tierLabel: String,
    @SerialName("lease_ttl_s") val leaseTtlS: Int,
    val models: List<String>,
)

@Serializable
data class DeviceView(
    val id: String,
    val name: String,
    val tier: Int,
    val online: Boolean,
    val eligible: Boolean,
    @SerialName("last_heartbeat_at") val lastHeartbeatAt: String? = null,
    @SerialName("shards_completed") val shardsCompleted: Int = 0,
    @SerialName("earned_mc") val earnedMc: Long = 0,
)

// --- shards -----------------------------------------------------------------

@Serializable
data class ShardAssignment(
    @SerialName("shard_id") val shardId: String,
    @SerialName("job_id") val jobId: String,
    val index: Int,
    @SerialName("model_ref") val modelRef: String,
    val tier: Int,
    val items: List<JsonElement>,
    @SerialName("lease_deadline_ms") val leaseDeadlineMs: Long,
    @SerialName("lease_ttl_s") val leaseTtlS: Int,
)

@Serializable
data class DeviceLog(
    val ts: String,
    val level: String,
    val event: String,
    val detail: String? = null,
    @SerialName("thermal_status") val thermalStatus: Int? = null,
    @SerialName("battery_pct") val batteryPct: Int? = null,
)

@Serializable
data class ShardResult(
    val outputs: List<JsonElement>,
    @SerialName("duration_ms") val durationMs: Int,
    val delegate: String,
    @SerialName("device_logs") val deviceLogs: List<DeviceLog> = emptyList(),
)

@Serializable
data class ShardFailure(val reason: String, val retryable: Boolean = true)

@Serializable
data class ClaimAck(
    val accepted: Boolean,
    @SerialName("payout_mc") val payoutMc: Long = 0,
    @SerialName("balance_mc") val balanceMc: Long = 0,
    val detail: String = "",
)

@Serializable
data class LeaseRenewal(@SerialName("lease_deadline_ms") val leaseDeadlineMs: Long)

// --- jobs -------------------------------------------------------------------

@Serializable
data class JobSubmit(
    @SerialName("model_ref") val modelRef: String,
    val inputs: List<JsonElement>,
    @SerialName("shard_size") val shardSize: Int,
    @SerialName("min_tier") val minTier: Int = 0,
)

@Serializable
data class ShardView(
    val index: Int,
    val state: String,
    @SerialName("device_id") val deviceId: String? = null,
    val attempts: Int = 0,
)

@Serializable
data class JobView(
    val id: String,
    @SerialName("model_ref") val modelRef: String,
    val status: String,
    @SerialName("min_tier") val minTier: Int,
    @SerialName("shard_count") val shardCount: Int,
    @SerialName("shards_done") val shardsDone: Int,
    @SerialName("shards_failed") val shardsFailed: Int,
    @SerialName("shards_claimed") val shardsClaimed: Int,
    @SerialName("cost_mc") val costMc: Long,
    @SerialName("created_at") val createdAt: String,
    @SerialName("completed_at") val completedAt: String? = null,
    val shards: List<ShardView> = emptyList(),
)

@Serializable
data class JobEvent(
    @SerialName("job_id") val jobId: String,
    val type: String,
    val index: Int? = null,
    @SerialName("device_id") val deviceId: String? = null,
    @SerialName("shards_done") val shardsDone: Int = 0,
    @SerialName("shards_failed") val shardsFailed: Int = 0,
    @SerialName("shard_count") val shardCount: Int = 0,
)

@Serializable
data class JobResult(
    @SerialName("job_id") val jobId: String,
    val status: String,
    @SerialName("model_ref") val modelRef: String,
    @SerialName("item_count") val itemCount: Int,
    val outputs: List<JsonElement>,
    @SerialName("failed_shards") val failedShards: List<Int> = emptyList(),
)

@Serializable
data class ModelInfo(
    val ref: String,
    val description: String,
    @SerialName("min_tier") val minTier: Int,
    @SerialName("min_tier_label") val minTierLabel: String,
    @SerialName("input_kind") val inputKind: String,
    @SerialName("output_dim") val outputDim: Int,
    @SerialName("price_per_item_mc") val pricePerItemMc: Int,
)

@Serializable
data class Account(
    @SerialName("user_id") val userId: String,
    val email: String,
    @SerialName("balance_mc") val balanceMc: Long,
    @SerialName("balance_display") val balanceDisplay: String,
    @SerialName("earned_mc") val earnedMc: Long,
    @SerialName("spent_mc") val spentMc: Long,
)
