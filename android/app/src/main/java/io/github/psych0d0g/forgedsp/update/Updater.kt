package io.github.psych0d0g.forgedsp.update

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings as SystemSettings
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * Update of the app from the GitHub releases (#29). The latest release carries
 * forge-dsp-<version>-android.apk, signed with the same release key; Android installs it only over
 * an app with that signature. The version code comes from the version (see [code]), so a release
 * is newer than every build before it. Installed through the PackageInstaller: the first time
 * Android asks (and wants "install unknown apps" for this app), from Android 12 on the following
 * updates go through without a question, since the app is then the installer of itself.
 */
object Updater {
    private const val API = "https://api.github.com/repos/psych0d0g/forge-dsp-gui-ng/releases/latest"
    private const val ACTION = "io.github.psych0d0g.forgedsp.action.INSTALLED"
    private const val PREFS = "update"
    private const val EVERY_MS = 6 * 3600_000L

    enum class State { IDLE, CHECKING, CURRENT, AVAILABLE, DOWNLOADING, INSTALLING, PERMISSION, ERROR }

    @Volatile var state = State.IDLE; private set
    @Volatile var latest: String? = null; private set
    @Volatile var progress = 0; private set     // percent while DOWNLOADING
    @Volatile var err = ""; private set
    private var apkUrl: String? = null
    private var apkSize = 0L
    private var sumsUrl: String? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** version code from x.y.z and the commits since the tag: x.y.z-n -> ((x*100+y)*100+z)*1000+n */
    fun code(version: String, commits: Int = 0): Long? {
        val m = Regex("""^v?(\d+)\.(\d+)\.(\d+)""").find(version) ?: return null
        val (x, y, z) = m.destructured
        return ((x.toLong() * 100 + y.toLong()) * 100 + z.toLong()) * 1000 + commits.coerceIn(0, 999)
    }

    private fun installed(ctx: Context): Pair<String, Long> {
        val p = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
        @Suppress("DEPRECATION")
        val c = if (Build.VERSION.SDK_INT >= 28) p.longVersionCode else p.versionCode.toLong()
        return (p.versionName ?: "?") to c
    }

    fun json(ctx: Context): String = JSONObject().apply {
        val (name, c) = installed(ctx)
        put("current", name); put("code", c)
        put("state", state.name); put("latest", latest ?: JSONObject.NULL)
        put("progress", progress); put("error", err)
    }.toString()

    /** at the start of the GUI: at most every six hours */
    fun checkSometimes(ctx: Context) {
        val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        if (now - p.getLong("checked", 0) < EVERY_MS) return
        p.edit().putLong("checked", now).apply()
        check(ctx)
    }

    fun check(ctx: Context) {
        if (state == State.CHECKING || state == State.DOWNLOADING || state == State.INSTALLING) return
        state = State.CHECKING; err = ""
        val app = ctx.applicationContext
        scope.launch {
            runCatching {
                val r = JSONObject(get(API).bufferedReader().use { it.readText() })
                val tag = r.getString("tag_name")
                val assets = r.getJSONArray("assets")
                apkUrl = null; sumsUrl = null
                for (i in 0 until assets.length()) {
                    val a = assets.getJSONObject(i)
                    val n = a.getString("name")
                    if (n.endsWith("-android.apk")) { apkUrl = a.getString("browser_download_url"); apkSize = a.optLong("size") }
                    if (n == "SHA256SUMS") sumsUrl = a.getString("browser_download_url")
                }
                latest = tag.removePrefix("v")
                val theirs = code(tag) ?: throw IllegalStateException("no version in $tag")
                state = if (apkUrl != null && theirs > installed(app).second) State.AVAILABLE else State.CURRENT
            }.onFailure { fail(it) }
        }
    }

    fun install(ctx: Context) {
        if (state != State.AVAILABLE && state != State.PERMISSION && state != State.ERROR) return
        val url = apkUrl ?: return
        val app = ctx.applicationContext
        if (Build.VERSION.SDK_INT >= 26 && !app.packageManager.canRequestPackageInstalls()) {
            state = State.PERMISSION
            runCatching {
                ctx.startActivity(Intent(SystemSettings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${app.packageName}"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            return
        }
        state = State.DOWNLOADING; progress = 0; err = ""
        scope.launch {
            runCatching {
                val f = File(app.cacheDir, "update.apk")
                val md = MessageDigest.getInstance("SHA-256")
                get(url).use { inp ->
                    f.outputStream().use { out ->
                        val buf = ByteArray(64 * 1024)
                        var done = 0L
                        while (true) {
                            val n = inp.read(buf); if (n < 0) break
                            out.write(buf, 0, n); md.update(buf, 0, n); done += n
                            if (apkSize > 0) progress = (done * 100 / apkSize).toInt()
                        }
                    }
                }
                // SHA256SUMS of the release must list the APK and match (#38: a missing line was skipped silently;
                // the signature check of Android stays in addition)
                val sum = md.digest().joinToString("") { "%02x".format(it) }
                val u = sumsUrl ?: throw IllegalStateException("the release has no SHA256SUMS")
                val name = url.substringAfterLast('/')
                val text = runCatching { get(u).bufferedReader().use { it.readText() } }.getOrNull()
                    ?: throw IllegalStateException("SHA256SUMS of the release could not be loaded")
                val line = text.lines().firstOrNull { it.trim().split(Regex("""\s+""")).lastOrNull()?.removePrefix("*") == name }
                    ?: throw IllegalStateException("SHA256SUMS of the release does not list $name")
                if (!line.trim().startsWith(sum)) throw IllegalStateException("checksum does not match")
                state = State.INSTALLING
                commit(app, f)
            }.onFailure { fail(it) }
        }
    }

    private fun commit(ctx: Context, apk: File) {
        val pi = ctx.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(ctx.packageName)
            setSize(apk.length())
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = pi.createSession(params)
        pi.openSession(id).use { s ->
            s.openWrite("app.apk", 0, apk.length()).use { out -> apk.inputStream().use { it.copyTo(out) }; s.fsync(out) }
            val rcv = object : BroadcastReceiver() {
                override fun onReceive(c: Context, i: Intent) {
                    when (val st = i.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
                        PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                            @Suppress("DEPRECATION")
                            val confirm = if (Build.VERSION.SDK_INT >= 33) i.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                                else i.getParcelableExtra(Intent.EXTRA_INTENT)
                            confirm?.let { c.startActivity(it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                        }
                        PackageInstaller.STATUS_SUCCESS -> { state = State.CURRENT; runCatching { c.unregisterReceiver(this) } }
                        else -> {
                            runCatching { c.unregisterReceiver(this) }
                            state = State.ERROR
                            err = i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "install failed ($st)"
                            if (st == PackageInstaller.STATUS_FAILURE_ABORTED) { state = State.AVAILABLE; err = "" }
                        }
                    }
                }
            }
            val f = IntentFilter(ACTION)
            if (Build.VERSION.SDK_INT >= 33) ctx.registerReceiver(rcv, f, Context.RECEIVER_NOT_EXPORTED)
            else @Suppress("UnspecifiedRegisterReceiverFlag") ctx.registerReceiver(rcv, f)
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            val intent = PendingIntent.getBroadcast(ctx, id, Intent(ACTION).setPackage(ctx.packageName), flags)
            s.commit(intent.intentSender)
        }
    }

    private fun fail(e: Throwable) {
        state = State.ERROR
        err = e.message ?: e.toString()
    }

    private fun get(url: String) = (URL(url).openConnection() as HttpURLConnection).run {
        connectTimeout = 15000; readTimeout = 30000
        instanceFollowRedirects = true
        setRequestProperty("Accept", "application/vnd.github+json")
        setRequestProperty("User-Agent", "forge-dsp-android")
        if (responseCode !in 200..299) throw IllegalStateException("HTTP $responseCode")
        inputStream
    }
}
