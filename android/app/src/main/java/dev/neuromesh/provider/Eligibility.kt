package dev.neuromesh.provider

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import dev.neuromesh.infer.InferenceEngine
import dev.neuromesh.infer.ModelRegistry
import dev.neuromesh.net.Capability

/**
 * The owner's device comes first.
 *
 * Compute happens only while charging, on Wi-Fi, with the screen off, and below
 * the thermal ceiling. All four, every time, re-checked before every claim --
 * not once at startup. The moment any of them goes false the loop stops, and
 * whatever shard was in flight is abandoned rather than finished.
 */
class Eligibility(private val context: Context, private val engine: InferenceEngine) {

    /** Compute up to and including LIGHT. At MODERATE the phone is already
     *  throttling and the owner would feel it -- back off before that. */
    val thermalCeiling: Int = PowerManager.THERMAL_STATUS_LIGHT

    data class State(
        val charging: Boolean,
        val wifi: Boolean,
        val screenOff: Boolean,
        val thermalStatus: Int,
        val batteryPct: Int,
    ) {
        val eligible: Boolean
            get() = charging && wifi && screenOff && thermalStatus <= PowerManager.THERMAL_STATUS_LIGHT

        /** What to show the owner, in their words rather than ours. */
        val reason: String?
            get() = when {
                !charging -> "Plug the phone in to start earning"
                !wifi -> "Waiting for Wi-Fi"
                !screenOff -> "Runs when the screen is off"
                thermalStatus > PowerManager.THERMAL_STATUS_LIGHT -> "Cooling down"
                else -> null
            }
    }

    fun state(): State = State(
        charging = isCharging(),
        wifi = isOnUnmeteredWifi(),
        screenOff = isScreenOff(),
        thermalStatus = thermalStatus(),
        batteryPct = batteryPercent(),
    )

    private fun isCharging(): Boolean {
        val manager = context.getSystemService(BatteryManager::class.java)
        if (manager != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return manager.isCharging
        }
        val status = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            ?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        return status == BatteryManager.BATTERY_STATUS_CHARGING ||
            status == BatteryManager.BATTERY_STATUS_FULL
    }

    /**
     * Wi-Fi *and* unmetered. A hotspot the owner pays for by the gigabyte is
     * not somewhere to download shard payloads all night.
     */
    private fun isOnUnmeteredWifi(): Boolean {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return false
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) &&
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) &&
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    private fun isScreenOff(): Boolean {
        val pm = context.getSystemService(PowerManager::class.java) ?: return false
        return !pm.isInteractive
    }

    fun thermalStatus(): Int {
        val pm = context.getSystemService(PowerManager::class.java) ?: return 0
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) pm.currentThermalStatus else 0
    }

    private fun batteryPercent(): Int {
        val manager = context.getSystemService(BatteryManager::class.java) ?: return 100
        return manager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
            .coerceIn(0, 100)
    }

    /**
     * What this phone can actually do, measured rather than claimed: real free
     * memory, and the delegates that genuinely initialised.
     */
    fun capability(): Capability {
        val probe = engine.probe()
        return Capability(
            availableRamMb = availableRamMb(),
            qnnDelegate = probe.qnnDelegate,
            gpuDelegate = probe.gpuDelegate,
            quantizations = ModelRegistry.quantizations(),
            soc = soc(),
            npuTops = null,
        )
    }

    private fun soc(): String =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            "${Build.SOC_MANUFACTURER} ${Build.SOC_MODEL}".trim()
        } else {
            "${Build.MANUFACTURER} ${Build.HARDWARE}".trim()
        }

    private fun availableRamMb(): Int {
        val am = context.getSystemService(ActivityManager::class.java) ?: return 0
        val info = ActivityManager.MemoryInfo()
        am.getMemoryInfo(info)
        return (info.availMem / (1024 * 1024)).toInt()
    }
}
