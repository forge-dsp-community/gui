package io.github.psych0d0g.forgedsp.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.database.ContentObserver
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.media.VolumeProvider
import android.media.session.MediaSession
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings as SystemSettings
import kotlin.math.roundToInt

/**
 * The Android volume on the Forge master (#29), as set in [Settings.volumeMode]:
 *  - MIRROR: the media volume (STREAM_MUSIC) sets the master; index/max over rangeDb, 0 = muted.
 *    Android still attenuates as well (digital USB audio: before the Forge).
 *  - MIRROR_FIXED: Android stays one step below its top (full level to the Forge, a step up is
 *    still seen); every change is a step of stepDb on the master, then Android goes back.
 *  - REMOTE: a media session with remote playback and its own volume provider (as a cast device):
 *    the volume keys set the Forge while no other media session takes them.
 *  - ABSOLUTE (#31, system installation only): absolute volume on the USB audio device, Android
 *    plays at 0 dB and its index sets the master ([AbsoluteVolume]); only while the Forge answers.
 * The own ROM will do this natively; this is for head units with a stock system.
 */
class VolumeControl(private val svc: ForgeService) {
    private val am = svc.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val main = Handler(Looper.getMainLooper())
    private var mode = VolumeMode.OFF
    private var s = Settings()
    private var last = -1                // last seen index of STREAM_MUSIC
    private var virtualDb = 0.0          // MIRROR_FIXED / REMOTE: the master in dB
    private var session: MediaSession? = null
    val absolute = AbsoluteVolume(svc) { i, mx -> main.post { fromIndex(i, mx) } }
    private val devices = object : AudioDeviceCallback() {
        // the XMOS (sound) may come up after the CP2102 (control)
        override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>) { if (mode == VolumeMode.ABSOLUTE) tryAbsolute() }
        override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>) {
            if (mode == VolumeMode.ABSOLUTE && AbsoluteVolume.usbOutput(am) == null) absolute.disable()
        }
    }

    private val max get() = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC).coerceAtLeast(1)
    private val index get() = am.getStreamVolume(AudioManager.STREAM_MUSIC)

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) {
            // android.media.VOLUME_CHANGED_ACTION (not public, but sent by every Android since 4)
            val stream = i.getIntExtra("android.media.EXTRA_VOLUME_STREAM_TYPE", -1)
            if (stream == -1 || stream == AudioManager.STREAM_MUSIC) changed()
        }
    }
    private val observer = object : ContentObserver(main) {
        override fun onChange(selfChange: Boolean) = changed()
    }
    private var listening = false

    fun apply(new: Settings) {
        val was = mode
        s = new
        mode = new.volumeMode
        if (was != mode) {
            stopListening()
            session?.release()
            session = null
            if (was == VolumeMode.ABSOLUTE) { absolute.disable(); am.unregisterAudioDeviceCallback(devices) }
            last = -1
            when (mode) {
                VolumeMode.OFF -> {}
                VolumeMode.ABSOLUTE -> am.registerAudioDeviceCallback(devices, main)
                VolumeMode.MIRROR, VolumeMode.MIRROR_FIXED -> startListening()
                VolumeMode.REMOTE -> main.post { startSession() }
            }
        }
        onConnected()
    }

    /** connected (again): the master from the Forge as the start, or the Android volume to the Forge */
    fun onConnected() = main.post {
        svc.masterValue?.let { virtualDb = masterDb(it) }
        when (mode) {
            VolumeMode.MIRROR -> { last = -1; changed() }
            VolumeMode.MIRROR_FIXED -> park()
            VolumeMode.REMOTE -> session?.let { startSession() }
            VolumeMode.ABSOLUTE -> tryAbsolute()
            VolumeMode.OFF -> {}
        }
    }

    /** the Forge does not answer (any more): no absolute volume without control, at once */
    fun onOffline() = absolute.disable()

    /** the master changed on the Forge (GUI, other apps, the Forge itself) */
    fun onMaster(value: Int) = main.post {
        if (mode == VolumeMode.ABSOLUTE) absolute.follow(toIndex(value, absolute.max))
    }

    private fun tryAbsolute() {
        if (svc.status != ForgeService.Status.ONLINE || absolute.active) return
        absolute.enable(svc.masterValue?.let { toIndex(it, absolute.max) } ?: absolute.max)
        svc.stateChanged()
    }

    /** index of the Android volume -> master, as MIRROR: index/max over rangeDb, 0 = muted */
    private fun fromIndex(i: Int, mx: Int) {
        svc.setMaster(if (i <= 0) 0 else masterValue(-s.rangeDb * (1.0 - i.toDouble() / mx.coerceAtLeast(1))))
    }

    private fun toIndex(value: Int, mx: Int): Int {
        if (value == 0) return 0
        val db = masterDb(value).coerceIn(-s.rangeDb.toDouble(), 0.0)
        return (mx * (1.0 + db / s.rangeDb)).roundToInt().coerceIn(1, mx)
    }

    private fun startListening() {
        if (listening) return
        listening = true
        val f = IntentFilter("android.media.VOLUME_CHANGED_ACTION")
        if (Build.VERSION.SDK_INT >= 33) svc.registerReceiver(receiver, f, Context.RECEIVER_EXPORTED)
        else @Suppress("UnspecifiedRegisterReceiverFlag") svc.registerReceiver(receiver, f)
        svc.contentResolver.registerContentObserver(SystemSettings.System.CONTENT_URI, true, observer)
        main.post { if (mode == VolumeMode.MIRROR_FIXED) park() else changed() }
    }

    private fun stopListening() {
        if (!listening) return
        listening = false
        runCatching { svc.unregisterReceiver(receiver) }
        runCatching { svc.contentResolver.unregisterContentObserver(observer) }
    }

    /** MIRROR_FIXED: Android one step below the top */
    private fun park() {
        val ref = (max - 1).coerceAtLeast(0)
        last = ref
        // our own change comes back as an event too; it reads index == last and is ignored
        if (index != ref) runCatching { am.setStreamVolume(AudioManager.STREAM_MUSIC, ref, 0) }
    }

    private fun changed() {
        val i = index
        if (i == last) return
        when (mode) {
            VolumeMode.MIRROR -> {
                last = i
                svc.setMaster(if (i == 0) 0 else masterValue(-s.rangeDb * (1.0 - i.toDouble() / max)))
            }
            VolumeMode.MIRROR_FIXED -> {
                val delta = if (last < 0) 0 else i - last
                if (delta != 0) {
                    virtualDb = (virtualDb + delta * s.stepDb).coerceIn(-s.rangeDb.toDouble(), 0.0)
                    svc.setMaster(if (virtualDb <= -s.rangeDb) 0 else masterValue(virtualDb))
                }
                park()
            }
            else -> {}
        }
    }

    // --- REMOTE --------------------------------------------------------------------------------------
    private fun steps() = s.rangeDb * 2   // 0.5 dB per step, as the master

    private fun startSession() {
        val n = steps()
        val cur = svc.masterValue?.let { ((masterDb(it) + s.rangeDb) * 2).roundToInt().coerceIn(0, n) } ?: n
        val provider = object : VolumeProvider(VOLUME_CONTROL_ABSOLUTE, n, cur) {
            override fun onSetVolumeTo(volume: Int) = set(volume)
            override fun onAdjustVolume(direction: Int) = set(currentVolume + direction * (s.stepDb * 2).roundToInt().coerceAtLeast(1))
            private fun set(v: Int) {
                val x = v.coerceIn(0, n)
                currentVolume = x
                svc.setMaster(if (x == 0) 0 else masterValue(x / 2.0 - s.rangeDb))
            }
        }
        val ms = session ?: MediaSession(svc, "ForgeDSP").also { session = it }
        ms.setPlaybackToRemote(provider)
        ms.isActive = true
    }

    fun stop() {
        absolute.disable()
        runCatching { am.unregisterAudioDeviceCallback(devices) }
        stopListening()
        session?.release()
        session = null
    }
}
