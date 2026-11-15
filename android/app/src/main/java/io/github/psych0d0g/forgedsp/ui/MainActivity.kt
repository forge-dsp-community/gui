package io.github.psych0d0g.forgedsp.ui

import android.annotation.SuppressLint
import android.Manifest
import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Build
import android.os.IBinder
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewClientCompat
import io.github.psych0d0g.forgedsp.service.ForgeService

/**
 * The web GUI (bundled, offline) in a WebView: https://appassets.androidplatform.net/assets/index.html
 * is a secure context (crypto.subtle, localStorage). The USB port comes through [Bridge]; file
 * inputs open the system's file picker, downloads go through "save as" (Storage Access Framework).
 * The connection itself belongs to [ForgeService]; the activity binds to it while it is open.
 */
class MainActivity : ComponentActivity() {
    private lateinit var web: WebView
    private lateinit var bridge: Bridge
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var pendingSave: Pair<ByteArray, (String) -> Unit>? = null
    @Volatile var service: ForgeService? = null
        private set
    private val conn = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, b: IBinder) { service = (b as ForgeService.LocalBinder).service }
        override fun onServiceDisconnected(name: ComponentName) { service = null }
    }

    private val pickFile = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(r.resultCode, r.data))
        fileCallback = null
    }
    private val createFile = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        val (data, done) = pendingSave ?: return@registerForActivityResult
        pendingSave = null
        val uri = r.data?.data
        if (r.resultCode != Activity.RESULT_OK || uri == null) { done("cancel"); return@registerForActivityResult }
        done(runCatching { contentResolver.openOutputStream(uri)!!.use { it.write(data) } }.exceptionOrNull()?.message ?: "")
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web)
        fullscreen()
        val assets = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this)).build()
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.webViewClient = object : WebViewClientCompat() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                assets.shouldInterceptRequest(request.url)
            // links out of the app (GitHub, data sheets) go to the browser; the app stays as it is
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                if (request.url.host == "appassets.androidplatform.net") return false
                runCatching { startActivity(Intent(Intent.ACTION_VIEW, request.url).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                return true
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                return runCatching { pickFile.launch(params.createIntent()) }.isSuccess.also { if (!it) { fileCallback = null; callback.onReceiveValue(null) } }
            }
        }
        bindService(Intent(this, ForgeService::class.java), conn, Context.BIND_AUTO_CREATE)
        ForgeService.start(this)
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)   // the service's notification
        bridge = Bridge(this, web)
        web.addJavascriptInterface(bridge, "ForgeAndroid")
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            // back: to the car page first, then leave
            override fun handleOnBackPressed() {
                web.evaluateJavascript("(function(){var a=window.__forgeAndroidBack;return a?a():false})()") { r ->
                    if (r != "true") { isEnabled = false; onBackPressedDispatcher.onBackPressed(); isEnabled = true }
                }
            }
        })
        if (savedInstanceState == null) web.loadUrl("https://appassets.androidplatform.net/assets/index.html")
        else web.restoreState(savedInstanceState)
    }

    /** no status bar on the head unit (1024x600 is little room); a swipe from the top shows it for a moment */
    private fun fullscreen() {
        WindowCompat.getInsetsController(window, window.decorView).apply {
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            hide(WindowInsetsCompat.Type.statusBars())
        }
    }

    // dialogs (file picker, permission) and the swipe bring it back; hide it again with the focus
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) fullscreen()
    }

    /** "save as" for a download of the GUI; done("") = written, "cancel", or an error text */
    fun saveAs(data: ByteArray, name: String, mime: String, done: (String) -> Unit) {
        pendingSave?.second?.invoke("cancel")
        pendingSave = data to done
        val i = Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType(mime.ifBlank { "application/octet-stream" }).putExtra(Intent.EXTRA_TITLE, name)
        runCatching { createFile.launch(i) }.onFailure { pendingSave = null; done(it.message ?: "no file app") }
    }

    // the Forge was plugged in (USB_DEVICE_ATTACHED, singleTask)
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        bridge.attached()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    override fun onDestroy() {
        bridge.shutdown()
        runCatching { unbindService(conn) }
        web.destroy()
        super.onDestroy()
    }
}
