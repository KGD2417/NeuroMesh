package dev.neuromesh

import android.app.Application
import dev.neuromesh.data.Session
import dev.neuromesh.infer.InferenceEngine
import dev.neuromesh.net.Api

/**
 * Three singletons, wired by hand.
 *
 * A dependency-injection framework for three objects would be more moving parts
 * than the objects.
 */
class NeuroMeshApp : Application() {

    val session: Session by lazy { Session(this) }
    val api: Api by lazy { Api(session) }
    val engine: InferenceEngine by lazy { InferenceEngine(this) }
}
