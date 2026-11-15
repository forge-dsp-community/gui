package io.github.psych0d0g.forgedsp.service

import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import java.lang.reflect.Proxy
import java.util.concurrent.Executor

/**
 * Absolute volume on the USB audio device of the Forge (#31): Android sends the sound unattenuated
 * (0 dB) and reports the volume index instead, which sets the Forge master. As Android does it for
 * Bluetooth (AVRCP) and HDMI-CEC. System API (AudioDeviceVolumeManager.setDeviceAbsoluteVolumeBehavior,
 * Android 13+), so by reflection; needs the app as a privileged system app with MODIFY_AUDIO_ROUTING
 * (or MODIFY_AUDIO_SETTINGS_PRIVILEGED / BLUETOOTH_PRIVILEGED) and on the hidden API allowlist
 * (android/rom/). Only while the control connection is up: without it the device goes back to
 * VARIABLE at once, else USB sound would play at full level.
 */
class AbsoluteVolume(private val ctx: Context, private val onIndex: (index: Int, max: Int) -> Unit) {
    private val am = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private var device: Any? = null          // AudioDeviceAttributes of the USB output while absolute
    @Volatile var active = false; private set
    @Volatile var error = ""; private set

    companion object {
        private val PERMISSIONS = listOf("android.permission.MODIFY_AUDIO_ROUTING",
            "android.permission.MODIFY_AUDIO_SETTINGS_PRIVILEGED", "android.permission.BLUETOOTH_PRIVILEGED")
        private const val VARIABLE = 0       // AudioManager.DEVICE_VOLUME_BEHAVIOR_VARIABLE

        /** the system lets the app do it: Android 13+, a privileged permission, the API reachable */
        fun available(ctx: Context): Boolean =
            Build.VERSION.SDK_INT >= 33 &&
                PERMISSIONS.any { ctx.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED } &&
                runCatching { Class.forName("android.media.AudioDeviceVolumeManager").getConstructor(Context::class.java) }.isSuccess

        /** the USB audio output (the XMOS of the Forge) */
        fun usbOutput(am: AudioManager): AudioDeviceInfo? = am.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            .filter { it.type == AudioDeviceInfo.TYPE_USB_DEVICE || it.type == AudioDeviceInfo.TYPE_USB_HEADSET }
            .sortedByDescending { it.productName?.toString()?.contains("forge", true) == true }
            .firstOrNull()
    }

    val max get() = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC).coerceAtLeast(1)

    /** absolute on the USB output, starting at [index]; false (and [error]) if not possible */
    fun enable(index: Int): Boolean {
        if (active) return true
        val out = usbOutput(am) ?: return fail("no USB audio output")
        return runCatching {
            val cAttr = Class.forName("android.media.AudioDeviceAttributes")
            val cInfo = Class.forName("android.media.VolumeInfo")
            val cBuilder = Class.forName("android.media.VolumeInfo\$Builder")
            val cMgr = Class.forName("android.media.AudioDeviceVolumeManager")
            val cListener = Class.forName("android.media.AudioDeviceVolumeManager\$OnAudioDeviceVolumeChangedListener")
            val attr = cAttr.getConstructor(AudioDeviceInfo::class.java).newInstance(out)
            val int = Int::class.javaPrimitiveType
            val b = cBuilder.getConstructor(int).newInstance(AudioManager.STREAM_MUSIC)
            cBuilder.getMethod("setMinVolumeIndex", int).invoke(b, 0)
            cBuilder.getMethod("setMaxVolumeIndex", int).invoke(b, max)
            cBuilder.getMethod("setVolumeIndex", int).invoke(b, index.coerceIn(0, max))
            val info = cBuilder.getMethod("build").invoke(b)
            val getIndex = cInfo.getMethod("getVolumeIndex")
            val getMax = runCatching { cInfo.getMethod("getMaxVolumeIndex") }.getOrNull()
            val listener = Proxy.newProxyInstance(cListener.classLoader, arrayOf(cListener)) { self, m, args ->
                when (m.name) {
                    "onAudioDeviceVolumeChanged" -> {
                        val vi = args?.getOrNull(1)
                        if (vi != null && active) {
                            val i = getIndex.invoke(vi) as Int
                            val mx = (getMax?.invoke(vi) as? Int)?.takeIf { it > 0 } ?: max
                            onIndex(i, mx)
                        }
                        null
                    }
                    "equals" -> self === args?.getOrNull(0)
                    "hashCode" -> System.identityHashCode(self)
                    "toString" -> "ForgeAbsoluteVolume"
                    else -> null
                }
            }
            val mgr = cMgr.getConstructor(Context::class.java).newInstance(ctx)
            // the order of the arguments changed between Android versions: fill them by type
            val set = cMgr.methods.firstOrNull { it.name == "setDeviceAbsoluteVolumeBehavior" }
                ?: throw NoSuchMethodException("setDeviceAbsoluteVolumeBehavior")
            val main = Executor { r -> android.os.Handler(android.os.Looper.getMainLooper()).post(r) }
            val params = set.parameterTypes.map { t ->
                when {
                    t == cAttr -> attr
                    t == cInfo -> info
                    t == Executor::class.java -> main
                    t == cListener -> listener
                    t == Boolean::class.javaPrimitiveType -> false   // handlesVolumeAdjustment: Android steps, we follow
                    else -> throw IllegalStateException("unknown parameter ${t.name}")
                }
            }
            set.invoke(mgr, *params.toTypedArray())
            device = attr
            active = true
            error = ""
            runCatching { am.setStreamVolume(AudioManager.STREAM_MUSIC, index.coerceIn(0, max), 0) }
            true
        }.getOrElse { fail((it.cause ?: it).let { e -> e.message ?: e.toString() }) }
    }

    /** back to the normal (variable) volume on the USB output */
    fun disable() {
        if (!active) return
        active = false
        val attr = device ?: return
        device = null
        runCatching {
            val cAttr = Class.forName("android.media.AudioDeviceAttributes")
            AudioManager::class.java.getMethod("setDeviceVolumeBehavior", cAttr, Int::class.javaPrimitiveType).invoke(am, attr, VARIABLE)
        }.onFailure { error = "reset: " + ((it.cause ?: it).message ?: it.toString()) }
    }

    /** the master changed on the Forge: the Android volume follows */
    fun follow(index: Int) {
        if (!active) return
        val i = index.coerceIn(0, max)
        if (am.getStreamVolume(AudioManager.STREAM_MUSIC) != i) runCatching { am.setStreamVolume(AudioManager.STREAM_MUSIC, i, 0) }
    }

    private fun fail(e: String): Boolean { error = e; active = false; return false }
}
