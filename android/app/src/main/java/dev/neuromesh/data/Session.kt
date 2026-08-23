package dev.neuromesh.data

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit

/**
 * Everything this install needs to remember: which orchestrator, who is logged
 * in, and -- if this phone has been paired as a provider -- its device key.
 *
 * The device key is the phone's identity in the fleet. It is minted once, at
 * pairing, and the server keeps only its sha256, so this file is the only copy
 * that exists.
 *
 * ponytail: app-private SharedPreferences, not EncryptedSharedPreferences. The
 * app sandbox is the boundary on a non-rooted phone; move to the keystore if
 * this ever holds anything with a payout attached.
 */
class Session(context: Context) {

    enum class Mode { UNSET, PROVIDER, CONSUMER }

    private val prefs: SharedPreferences =
        context.getSharedPreferences("neuromesh", Context.MODE_PRIVATE)

    // --- orchestrator -------------------------------------------------------

    fun orchestrator(): String =
        prefs.getString(KEY_ORCHESTRATOR, null) ?: dev.neuromesh.BuildConfig.DEFAULT_ORCHESTRATOR

    fun setOrchestrator(url: String) {
        prefs.edit { putString(KEY_ORCHESTRATOR, normalize(url)) }
    }

    private fun normalize(url: String): String {
        val trimmed = url.trim().trimEnd('/')
        return if (trimmed.startsWith("http")) trimmed else "http://$trimmed"
    }

    // --- account ------------------------------------------------------------

    fun accessToken(): String? = prefs.getString(KEY_ACCESS, null)
    fun refreshToken(): String? = prefs.getString(KEY_REFRESH, null)
    fun userId(): String? = prefs.getString(KEY_USER, null)
    fun email(): String? = prefs.getString(KEY_EMAIL, null)
    fun isLoggedIn(): Boolean = accessToken() != null

    fun saveTokens(access: String, refresh: String, userId: String, email: String? = null) {
        prefs.edit {
            putString(KEY_ACCESS, access)
            putString(KEY_REFRESH, refresh)
            putString(KEY_USER, userId)
            email?.let { putString(KEY_EMAIL, it) }
        }
    }

    // --- device -------------------------------------------------------------

    fun deviceKey(): String? = prefs.getString(KEY_DEVICE_KEY, null)
    fun deviceId(): String? = prefs.getString(KEY_DEVICE_ID, null)
    fun deviceName(): String? = prefs.getString(KEY_DEVICE_NAME, null)
    fun isPaired(): Boolean = deviceKey() != null

    fun saveDevice(id: String, key: String, name: String) {
        prefs.edit {
            putString(KEY_DEVICE_ID, id)
            putString(KEY_DEVICE_KEY, key)
            putString(KEY_DEVICE_NAME, name)
        }
    }

    fun clearDevice() {
        prefs.edit { remove(KEY_DEVICE_ID); remove(KEY_DEVICE_KEY); remove(KEY_DEVICE_NAME) }
    }

    // --- mode ---------------------------------------------------------------

    /** One APK, two modes. Picked at login, changeable without reinstalling. */
    fun mode(): Mode = Mode.valueOf(prefs.getString(KEY_MODE, Mode.UNSET.name)!!)

    fun setMode(mode: Mode) {
        prefs.edit { putString(KEY_MODE, mode.name) }
    }

    fun logout() {
        prefs.edit { clear() }
    }

    private companion object {
        const val KEY_ORCHESTRATOR = "orchestrator"
        const val KEY_ACCESS = "access_token"
        const val KEY_REFRESH = "refresh_token"
        const val KEY_USER = "user_id"
        const val KEY_EMAIL = "email"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_DEVICE_KEY = "device_key"
        const val KEY_DEVICE_NAME = "device_name"
        const val KEY_MODE = "mode"
    }
}
