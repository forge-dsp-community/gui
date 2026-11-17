package io.github.psych0d0g.forgedsp.ui

import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.lifecycle.lifecycleScope
import io.github.psych0d0g.forgedsp.service.AbsoluteVolume
import io.github.psych0d0g.forgedsp.service.ForgeService
import io.github.psych0d0g.forgedsp.service.VolumeMode
import io.github.psych0d0g.forgedsp.service.Settings
import io.github.psych0d0g.forgedsp.update.Updater
import io.github.psych0d0g.forgedsp.usb.UsbLink
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject

/**
 * window.ForgeAndroid for the GUI (src/lib/android.ts). The connection belongs to [ForgeService]
 * (#29): the GUI attaches to it, gets every byte from the Forge and writes through it. The methods
 * run on the WebView's JavaScript bridge thread; answers and bytes go back through
 * window.__forgeAndroid on the UI thread.
 */
class Bridge(private val act: MainActivity, private val web: WebView) {
    private fun js(code: String) = web.post { web.evaluateJavascript(code, null) }
    /** an event for the page; with a GUI that fetches (poll) it waits in the queue, else it is pushed */
    private fun event(name: String, vararg args: Any) {
        val a = org.json.JSONArray().put(name); args.forEach { a.put(it) }
        if (polling) { events.add(a.toString()); kick() }
        else js("window.__forgeAndroid&&__forgeAndroid.$name(${args.joinToString(",") { if (it is String) JSONObject.quote(it) else it.toString() }})")
    }
    private fun kick() { if (kicked.compareAndSet(false, true)) js("window.__forgeAndroid&&__forgeAndroid.kick&&__forgeAndroid.kick()") }
    private fun settle(id: Int, err: String) = event("settle", id, err)
    private val svc get() = act.service

    fun attached() = event("attached")

    /** bytes of the Forge for the page (#42): the page fetches them itself (poll), every few ms and on a
     *  kick. Pushed through evaluateJavascript they waited for the UI thread, which a slow frame (here
     *  a 27 s swap of the VM's software renderer) holds: the GUI then read its port registers as missing. */
    private val inbox = java.util.concurrent.ConcurrentLinkedQueue<ByteArray>()
    private val events = java.util.concurrent.ConcurrentLinkedQueue<String>()
    private val kicked = java.util.concurrent.atomic.AtomicBoolean(false)
    /** the page fetches (a GUI from before this has no poll and keeps getting everything pushed) */
    @Volatile private var polling = false

    /** bytes of the Forge as base64 */
    @JavascriptInterface
    fun poll(): String {
        polling = true
        kicked.set(false)
        if (inbox.isEmpty()) return ""
        val out = java.io.ByteArrayOutputStream()
        while (true) out.write(inbox.poll() ?: break)
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }
    /** queued events as a JSON array of [name, args...]; fetched after poll(), so the bytes come first */
    @JavascriptInterface
    fun pollEvents(): String {
        if (events.isEmpty()) return "[]"
        val a = StringBuilder("[")
        while (true) { val e = events.poll() ?: break; if (a.length > 1) a.append(','); a.append(e) }
        return a.append(']').toString()
    }

    @JavascriptInterface
    fun present(): Boolean = UsbLink.find(act) != null

    @JavascriptInterface
    fun open(id: Int, @Suppress("UNUSED_PARAMETER") baud: Int) {
        android.util.Log.i("ForgeAct", "open act=${act.hashCode()} bridge=${hashCode()} web=${web.hashCode()}")
        act.lifecycleScope.launch {
            val err = try {
                val driver = UsbLink.find(act) ?: throw IllegalStateException("no Forge at the USB port")
                if (!UsbLink.permit(act, driver.device)) throw IllegalStateException("USB permission refused")
                ForgeService.start(act)
                val s = withTimeoutOrNull(5000) { while (act.service == null) delay(50); act.service }
                    ?: throw IllegalStateException("service not available")
                // an activity on its way out (recreated after the USB permission dialog) does not take the
                // line from the new one (#42)
                android.util.Log.i("ForgeAct", "open permitted act=${act.hashCode()} current=${MainActivity.current === act} finishing=${act.isFinishing}")
                if (act.isFinishing || act.isDestroyed || MainActivity.current !== act) throw IllegalStateException("activity closed")
                s.attachGui(this@Bridge,
                    onData = { b -> if (polling) { inbox.add(b); kick() } else js("window.__forgeAndroid&&__forgeAndroid.data('${Base64.encodeToString(b, Base64.NO_WRAP)}')") },
                    onGone = { event("gone") })
                // the service connects first (handshake, fast rate); the GUI then talks through it.
                // Without an answer (e.g. a waiting bootloader) the GUI looks for itself.
                withTimeoutOrNull(8000) { while (s.status == ForgeService.Status.CONNECTING || s.status == ForgeService.Status.SEARCHING) delay(50) }
                ""
            } catch (e: Exception) {
                e.message ?: e.toString()
            }
            settle(id, err)
        }
    }

    @JavascriptInterface
    fun write(b64: String) { val s = svc; if (s == null) android.util.Log.i("ForgeAct", "write: no service"); s?.guiWrite(Base64.decode(b64, Base64.DEFAULT)) }

    /** raw bytes for the bootloader: the service keeps quiet meanwhile */
    @JavascriptInterface
    fun writeRaw(b64: String) { svc?.guiWriteRaw(Base64.decode(b64, Base64.DEFAULT)) }

    @JavascriptInterface
    fun setBaud(baud: Int) { svc?.setBaud(baud) }

    /** the service finds the rate of the Forge again (gui-ng#42) */
    @JavascriptInterface
    fun recover() { svc?.recover() }

    /** the GUI lets go; the service keeps the connection */
    @JavascriptInterface
    fun close() { svc?.detachGui(this); inbox.clear() }

    @JavascriptInterface
    fun save(id: Int, b64: String, name: String, mime: String) {
        val data = Base64.decode(b64, Base64.DEFAULT)
        web.post { act.saveAs(data, name, mime) { err -> settle(id, err) } }
    }

    /** settings of the service and what the GUI shows about it */
    @JavascriptInterface
    fun settings(): String {
        val s = svc
        val o = JSONObject((s?.settings ?: Settings.load(act)).toJson())
        o.put("status", s?.status?.name ?: "STOPPED")
        o.put("master", s?.master?.let { org.json.JSONArray(it) } ?: JSONObject.NULL)
        o.put("masterValue", s?.masterValue ?: JSONObject.NULL)
        // absolute volume (#31): only as a privileged system app
        o.put("absoluteAvailable", AbsoluteVolume.available(act))
        o.put("absoluteActive", s?.absolute?.active == true)
        o.put("absoluteError", s?.absolute?.error ?: "")
        return o.toString()
    }

    @JavascriptInterface
    fun setSettings(json: String): String {
        val cur = svc?.settings ?: Settings.load(act)
        val s = runCatching { Settings.fromJson(json, cur) }.getOrElse { return it.message ?: "bad settings" }
        if (s.volumeMode == VolumeMode.ABSOLUTE && cur.volumeMode != VolumeMode.ABSOLUTE && !AbsoluteVolume.available(act))
            return "absolute volume needs the app as a privileged system app"
        svc?.updateSettings(s) ?: Settings.save(act, s)
        return ""
    }

    /** read the volume block again (after the control blocks changed) */
    @JavascriptInterface
    fun refreshMaster() { act.lifecycleScope.launch { svc?.readMaster() } }

    /** self-update from the GitHub releases: state as JSON, check, download and install */
    @JavascriptInterface
    fun update(): String = Updater.json(act)

    @JavascriptInterface
    fun checkUpdate() = Updater.check(act)

    @JavascriptInterface
    fun installUpdate() { web.post { Updater.install(act) } }

    /** debug build: the test rig (android/test-rig, #33) may drive the GUI */
    @JavascriptInterface
    fun debuggable(): Boolean = act.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE != 0

    /** screen stays on (firmware update, gui-ng#41: screen off sends the app to the background) */
    @JavascriptInterface
    fun keepScreenOn(on: Boolean) {
        act.runOnUiThread {
            val f = android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
            if (on) act.window.addFlags(f) else act.window.clearFlags(f)
        }
    }

    fun shutdown() = close()
}
