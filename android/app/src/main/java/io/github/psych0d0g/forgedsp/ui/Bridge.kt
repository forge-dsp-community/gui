package io.github.psych0d0g.forgedsp.ui

import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.lifecycle.lifecycleScope
import io.github.psych0d0g.forgedsp.service.ForgeService
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
    private fun settle(id: Int, err: String) = js("window.__forgeAndroid&&__forgeAndroid.settle($id,${JSONObject.quote(err)})")
    private val svc get() = act.service

    fun attached() = js("window.__forgeAndroid&&__forgeAndroid.attached()")

    @JavascriptInterface
    fun present(): Boolean = UsbLink.find(act) != null

    @JavascriptInterface
    fun open(id: Int, @Suppress("UNUSED_PARAMETER") baud: Int) {
        act.lifecycleScope.launch {
            val err = try {
                val driver = UsbLink.find(act) ?: throw IllegalStateException("no Forge at the USB port")
                if (!UsbLink.permit(act, driver.device)) throw IllegalStateException("USB permission refused")
                ForgeService.start(act)
                val s = withTimeoutOrNull(5000) { while (act.service == null) delay(50); act.service }
                    ?: throw IllegalStateException("service not available")
                s.attachGui(
                    onData = { b -> js("window.__forgeAndroid&&__forgeAndroid.data('${Base64.encodeToString(b, Base64.NO_WRAP)}')") },
                    onGone = { js("window.__forgeAndroid&&__forgeAndroid.gone()") })
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
    fun write(b64: String) { svc?.guiWrite(Base64.decode(b64, Base64.DEFAULT)) }

    /** raw bytes for the bootloader: the service keeps quiet meanwhile */
    @JavascriptInterface
    fun writeRaw(b64: String) { svc?.guiWriteRaw(Base64.decode(b64, Base64.DEFAULT)) }

    @JavascriptInterface
    fun setBaud(baud: Int) { svc?.setBaud(baud) }

    /** the GUI lets go; the service keeps the connection */
    @JavascriptInterface
    fun close() { svc?.detachGui() }

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
        return o.toString()
    }

    @JavascriptInterface
    fun setSettings(json: String): String {
        val cur = svc?.settings ?: Settings.load(act)
        val s = runCatching { Settings.fromJson(json, cur) }.getOrElse { return it.message ?: "bad settings" }
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

    fun shutdown() = close()
}
