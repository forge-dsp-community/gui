package io.github.psych0d0g.forgedsp.service

import android.content.Context
import org.json.JSONObject

/** How the Android volume acts on the Forge (#29); the own ROM will do it natively later. */
enum class VolumeMode { OFF, MIRROR, MIRROR_FIXED, REMOTE, ABSOLUTE }

/** Settings of the service, shared with the GUI as JSON (Bridge.settings / setSettings). */
data class Settings(
    val volumeMode: VolumeMode = VolumeMode.OFF,
    /** dB between the lowest step above 0 and the top of the Android volume */
    val rangeDb: Int = 60,
    /** volume key step in MIRROR_FIXED, dB */
    val stepDb: Double = 1.0,
    /** start with the head unit (needs the USB permission "by default") */
    val autostart: Boolean = true,
) {
    fun toJson(): String = JSONObject().put("volumeMode", volumeMode.name).put("rangeDb", rangeDb)
        .put("stepDb", stepDb).put("autostart", autostart).toString()

    companion object {
        private const val PREFS = "forge"

        fun fromJson(s: String, base: Settings = Settings()): Settings {
            val o = JSONObject(s)
            return Settings(
                volumeMode = runCatching { VolumeMode.valueOf(o.optString("volumeMode", base.volumeMode.name)) }.getOrDefault(base.volumeMode),
                rangeDb = o.optInt("rangeDb", base.rangeDb).coerceIn(20, 100),
                stepDb = o.optDouble("stepDb", base.stepDb).coerceIn(0.5, 6.0),
                autostart = o.optBoolean("autostart", base.autostart),
            )
        }

        fun load(ctx: Context): Settings =
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("settings", null)?.let { runCatching { fromJson(it) }.getOrNull() } ?: Settings()

        fun save(ctx: Context, s: Settings) =
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("settings", s.toJson()).apply()
    }
}
