package dev.neuromesh.net

import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flowOn
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * Server-sent events from GET /jobs/{id}/events.
 *
 * Hand-rolled rather than pulling in an SSE library: the server sends one
 * frame shape, `data: {json}\n\n`, plus `: keepalive` comments. That is a
 * twenty-line parser, and the library would still need this reconnect loop.
 */
object EventStream {

    private const val TAG = "NeuroMesh/SSE"
    private val json = Json { ignoreUnknownKeys = true }

    fun jobEvents(api: Api, jobId: String): Flow<JobEvent> = callbackFlow {
        val client: OkHttpClient = api.httpClient().newBuilder()
            // An event stream is idle most of the time by design.
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .callTimeout(0, TimeUnit.MILLISECONDS)
            .build()

        val call = client.newCall(
            api.request("/jobs/$jobId/events")
                .header("Accept", "text/event-stream")
                .get()
                .build()
        )

        val worker = Thread({
            try {
                call.execute().use { response ->
                    if (!response.isSuccessful) {
                        close(Api.HttpError(response.code, ""))
                        return@use
                    }
                    val source = response.body?.source() ?: return@use
                    while (!source.exhausted()) {
                        val line = source.readUtf8LineStrict()
                        if (!line.startsWith("data: ")) continue  // ": keepalive"
                        val event = runCatching {
                            json.decodeFromString<JobEvent>(line.removePrefix("data: "))
                        }.getOrNull() ?: continue
                        trySend(event)
                        if (event.type == "job.completed" || event.type == "job.failed") break
                    }
                }
                close()
            } catch (e: Exception) {
                Log.w(TAG, "stream ended: ${e.message}")
                close(e)
            }
        }, "neuromesh-sse")
        worker.isDaemon = true
        worker.start()

        awaitClose { call.cancel() }
    }.flowOn(Dispatchers.IO)
}
