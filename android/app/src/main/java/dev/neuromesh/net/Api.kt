package dev.neuromesh.net

import dev.neuromesh.data.Session
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.suspendCoroutine

/**
 * The whole orchestrator API, over one OkHttp client.
 *
 * No Retrofit: there are fifteen calls, all of them JSON in / JSON out, and a
 * code-generating HTTP layer is more moving parts than the endpoint list is
 * worth.
 */
class Api(private val session: Session) {

    class HttpError(val code: Int, val body: String) :
        IOException("HTTP $code: ${body.take(400)}")

    @PublishedApi
    internal val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
        explicitNulls = false
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        // A claim that hangs must not wedge the loop for longer than the lease.
        .callTimeout(60, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    /** Shared with EventStream, which needs a client with no read timeout. */
    fun httpClient(): OkHttpClient = client

    fun baseUrl(): String = session.orchestrator().trimEnd('/')

    // --- auth ---------------------------------------------------------------

    suspend fun register(email: String, password: String): TokenPair =
        post("/auth/register", Credentials(email, password), auth = false)

    suspend fun login(email: String, password: String): TokenPair =
        post("/auth/login", Credentials(email, password), auth = false)

    suspend fun refresh(token: String): TokenPair =
        post("/auth/refresh", RefreshRequest(token), auth = false)

    // --- consumer -----------------------------------------------------------

    suspend fun models(): List<ModelInfo> = get("/jobs/models")

    suspend fun submitJob(body: JobSubmit): JobView = post("/jobs", body)

    suspend fun job(id: String): JobView = get("/jobs/$id")

    suspend fun jobResult(id: String): JobResult = get("/jobs/$id/result")

    suspend fun cancelJob(id: String): JobView = post("/jobs/$id/cancel", Unit)

    suspend fun account(): Account = get("/me")

    suspend fun myDevices(): List<DeviceView> = get("/me/devices")

    suspend fun pairingCode(): PairingCode = post("/devices/pairing-code", Unit)

    // --- provider -----------------------------------------------------------

    suspend fun registerDevice(body: DeviceRegister): DeviceCredentials =
        post("/devices/register", body, auth = false)

    suspend fun heartbeat(body: HeartbeatRequest): HeartbeatResponse =
        post("/devices/heartbeat", body, auth = false, device = true)

    /** null means the queue had nothing for this tier -- back off, do not spin. */
    suspend fun claim(): ShardAssignment? =
        postOrNull("/devices/claim", Unit, device = true)

    suspend fun renewLease(shardId: String): LeaseRenewal =
        post("/devices/shards/$shardId/renew", Unit, auth = false, device = true)

    suspend fun completeShard(shardId: String, body: ShardResult): ClaimAck =
        post("/devices/shards/$shardId/complete", body, auth = false, device = true)

    suspend fun failShard(shardId: String, body: ShardFailure): ClaimAck =
        post("/devices/shards/$shardId/fail", body, auth = false, device = true)

    suspend fun postLogs(logs: List<DeviceLog>) {
        postRaw("/devices/logs", json.encodeToString(logs), auth = false, device = true)
    }

    // --- plumbing -----------------------------------------------------------

    private suspend inline fun <reified T> get(path: String): T {
        val body = execute(request(path).get().build())
        return json.decodeFromString(body)
    }

    private suspend inline fun <reified B, reified T> post(
        path: String,
        body: B,
        auth: Boolean = true,
        device: Boolean = false,
    ): T {
        val text = postRaw(path, encode(body), auth, device)
        return json.decodeFromString(text)
    }

    private suspend inline fun <reified B, reified T> postOrNull(
        path: String,
        body: B,
        auth: Boolean = true,
        device: Boolean = false,
    ): T? {
        val text = postRaw(path, encode(body), auth, device)
        return if (text.isBlank()) null else json.decodeFromString(text)
    }

    inline fun <reified B> encode(body: B): String =
        if (body is Unit) "{}" else json.encodeToString(body)

    suspend fun postRaw(
        path: String,
        body: String,
        auth: Boolean = true,
        device: Boolean = false,
    ): String {
        val builder = request(path, auth, device)
        return execute(builder.post(body.toRequestBody(JSON_MEDIA)).build())
    }

    fun request(path: String, auth: Boolean = true, device: Boolean = false): Request.Builder {
        val b = Request.Builder().url(baseUrl() + path)
        if (auth) session.accessToken()?.let { b.header("Authorization", "Bearer $it") }
        if (device) session.deviceKey()?.let { b.header("X-Device-Key", it) }
        return b
    }

    /**
     * One-shot call. 204 comes back as an empty string, which is how an empty
     * queue is expressed: it is the normal case, not an error.
     */
    private suspend fun execute(request: Request): String = withContext(Dispatchers.IO) {
        val response = client.newCall(request).await()
        response.use {
            val text = it.body.string()
            when {
                it.code == 204 -> ""
                it.isSuccessful -> text
                else -> throw HttpError(it.code, text)
            }
        }
    }

    private suspend fun Call.await(): Response = suspendCoroutine { cont ->
        enqueue(object : Callback {
            override fun onResponse(call: Call, response: Response) = cont.resume(response)
            override fun onFailure(call: Call, e: IOException) = cont.resumeWithException(e)
        })
    }

    companion object {
        val JSON_MEDIA: okhttp3.MediaType = "application/json; charset=utf-8".toMediaType()
    }
}
