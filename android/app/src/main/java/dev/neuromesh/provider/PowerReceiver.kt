package dev.neuromesh.provider

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import dev.neuromesh.NeuroMeshApp
import dev.neuromesh.data.Session

/**
 * Plugging the phone in is the signal to start earning.
 *
 * Only for a phone that has already been paired and put into provider mode --
 * this never opts anyone in. Android 12+ can refuse a foreground service start
 * from the background, in which case the owner starts it from the app; that is
 * a worse experience, not a broken one, so the failure is logged and dropped.
 */
class PowerReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext as? NeuroMeshApp ?: return
        val session = app.session
        if (session.mode() != Session.Mode.PROVIDER || !session.isPaired()) return

        when (intent.action) {
            Intent.ACTION_POWER_CONNECTED, Intent.ACTION_BOOT_COMPLETED ->
                runCatching { ProviderService.start(context) }
                    .onFailure { Log.i(TAG, "cannot auto-start from background: ${it.message}") }

            Intent.ACTION_POWER_DISCONNECTED ->
                // Not stopped: the loop's own eligibility check will idle it,
                // and the owner may plug it back in a second later.
                ProviderState.update { it.copy(eligible = false, reason = "Unplugged") }
        }
    }

    private companion object {
        const val TAG = "NeuroMesh/Power"
    }
}
