package io.github.psych0d0g.forgedsp.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.database.ContentObserver
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
            last = -1
            when (mode) {
                VolumeMode.OFF -> {}
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
            VolumeMode.OFF -> {}
        }
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
        stopListening()
        session?.release()
        session = null
    }
}
