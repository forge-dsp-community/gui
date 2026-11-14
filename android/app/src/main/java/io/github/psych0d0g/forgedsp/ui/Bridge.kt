package io.github.psych0d0g.forgedsp.ui

import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.lifecycle.lifecycleScope
import io.github.psych0d0g.forgedsp.usb.UsbLink
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * window.ForgeAndroid for the GUI (src/lib/android.ts): the USB port of the Forge and "save as".
 * The methods run on the WebView's JavaScript bridge thread; answers and received bytes go back
 * through window.__forgeAndroid on the UI thread.
 */
class Bridge(private val act: MainActivity, private val web: WebView) {
    @Volatile private var link: UsbLink? = null

    private fun js(code: String) = web.post { web.evaluateJavascript(code, null) }
    private fun settle(id: Int, err: String) = js("window.__forgeAndroid&&__forgeAndroid.settle($id,${JSONObject.quote(err)})")

    fun attached() = js("window.__forgeAndroid&&__forgeAndroid.attached()")

    @JavascriptInterface
    fun present(): Boolean = UsbLink.find(act) != null

    @JavascriptInterface
    fun open(id: Int, baud: Int) {
        act.lifecycleScope.launch {
            val err = try {
                link?.close()
                link = null
                val driver = UsbLink.find(act) ?: throw IllegalStateException("no Forge at the USB port")
                if (!UsbLink.permit(act, driver.device)) throw IllegalStateException("USB permission refused")
                link = UsbLink.open(act, driver, baud,
                    onData = { b -> js("window.__forgeAndroid&&__forgeAndroid.data('${Base64.encodeToString(b, Base64.NO_WRAP)}')") },
                    onGone = { link = null; js("window.__forgeAndroid&&__forgeAndroid.gone()") })
                ""
            } catch (e: Exception) {
                e.message ?: e.toString()
            }
            settle(id, err)
        }
    }

    @JavascriptInterface
    fun write(b64: String) {
        val l = link ?: return
        runCatching { l.write(Base64.decode(b64, Base64.DEFAULT)) }
    }

    @JavascriptInterface
    fun setBaud(baud: Int) { runCatching { link?.setBaud(baud) } }

    @JavascriptInterface
    fun close() {
        link?.close()
        link = null
    }

    @JavascriptInterface
    fun save(id: Int, b64: String, name: String, mime: String) {
        val data = Base64.decode(b64, Base64.DEFAULT)
        web.post { act.saveAs(data, name, mime) { err -> settle(id, err) } }
    }

    fun shutdown() = close()
}
