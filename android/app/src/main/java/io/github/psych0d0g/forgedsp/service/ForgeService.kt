package io.github.psych0d0g.forgedsp.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.usb.UsbManager
import android.os.Binder
import android.os.Build
import android.os.IBinder
import io.github.psych0d0g.forgedsp.R
import io.github.psych0d0g.forgedsp.ui.MainActivity
import io.github.psych0d0g.forgedsp.usb.UsbLink
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import java.util.Locale
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The connection to the Forge (#29), a foreground service: it holds the USB port with or without the
 * GUI, keeps the fast rate alive, lets the Android volume act on the Forge master ([VolumeControl])
 * and carries the lines of other apps ([CommandReceiver]). The GUI binds to it and gets every byte
 * from the Forge; its own requests and the service's are told apart by register (both match
 * answers strictly). During a firmware update (the GUI sent system.boot or raw bytes) the service
 * sends nothing.
 */
class ForgeService : Service() {
    enum class Status { SEARCHING, NO_PERMISSION, CONNECTING, ONLINE, NO_ANSWER }

    inner class LocalBinder : Binder() { val service get() = this@ForgeService }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val binder = LocalBinder()
    @Volatile private var link: UsbLink? = null
    @Volatile var status = Status.SEARCHING
        private set
    @Volatile var quiet = false
        private set
    /** the GUI: every received byte; null = not attached */
    @Volatile private var gui: ((ByteArray) -> Unit)? = null
    private var guiGone: (() -> Unit)? = null
    private val statusListeners = CopyOnWriteArrayList<(Status) -> Unit>()
    @Volatile var settings: Settings = Settings()
        private set
    private var volume: VolumeControl? = null
    /** events of the volume block that make the master, null = none */
    @Volatile var master: List<Int>? = null
        private set
    @Volatile var masterValue: Int? = null
        private set
    private var lastLine = 0L
    private val connectLock = Mutex()
    private var keepalive: Job? = null

    // --- own requests: answers by register ------------------------------------------------------
    private class Waiter(val reg: String, val isSet: Boolean) {
        val wild = reg.endsWith("*")
        val prefix = reg.removeSuffix("*")
        val lines = mutableListOf<Line>()
        val done = CompletableDeferred<Unit>()
        @Volatile var last = System.currentTimeMillis()
        /** #33: only the kind of answer this request gets: a get "<" (or "!"), a set "*" (or "!"); an event
         *  or the confirmation of a GUI write to the same register answered a get of another app before */
        fun takes(l: Line): Boolean {
            val kindOk = if (isSet) l.kind == '*' || l.kind == '!' else l.kind == '<' || l.kind == '!'
            return kindOk && (if (wild) l.register.startsWith(prefix) else l.register == reg)
        }
    }
    private val waiters = CopyOnWriteArrayList<Waiter>()
    private val lineBuf = StringBuilder()

    private fun onBytes(b: ByteArray) {
        lastLine = System.currentTimeMillis()
        // bootloader (raw mode): every byte to the GUI as it comes
        if (quiet) { gui?.invoke(b); return }
        val toGui = java.io.ByteArrayOutputStream()
        synchronized(lineBuf) {
            for (x in b) {
                val c = (x.toInt() and 0xFF).toChar()
                if (c == '\n') {
                    val text = lineBuf.toString()
                    lineBuf.setLength(0)
                    val l = Line.parse(text)
                    // #33: a line that answers a request of the service or of another app is theirs; the GUI
                    // shares the line and took such answers for its own requests (lists read short, answers shifted)
                    val taken = l != null && onLine(l)
                    if (!taken) { toGui.write(text.toByteArray(Charsets.ISO_8859_1)); toGui.write('\n'.code) }
                } else if (c != '\r' && lineBuf.length < 4096) lineBuf.append(c)
            }
        }
        if (toGui.size() > 0) gui?.invoke(toGui.toByteArray())
    }

    /** handles one line; true if a request of the service or of another app took it */
    private fun onLine(l: Line): Boolean {
        var taken = false
        for (w in waiters) if (w.takes(l)) {
            synchronized(w.lines) { w.lines.add(l) }
            w.last = System.currentTimeMillis()
            if (!w.wild) w.done.complete(Unit)
            taken = true
        }
        if (l.isEvent) CommandReceiver.event(this, l.raw)
        if (master?.any { l.register == "event.$it.value" } == true) l.value?.trim()?.toIntOrNull()?.let { masterValue = it; notifyState(); volume?.onMaster(it) }
        return taken
    }

    /** writes one line or raw bytes; serialised, the port takes one write at a time */
    @Synchronized
    private fun send(data: ByteArray): Boolean {
        val l = link ?: return false
        return runCatching { l.write(data) }.isSuccess
    }

    /** a request of the service (or of another app): its answer lines; wildcard registers until quiet */
    suspend fun request(line: String, timeoutMs: Long = 1500, fromOutside: Boolean = false): List<Line> {
        if (quiet || status != Status.ONLINE && !fromOutside && status != Status.CONNECTING) return emptyList()
        val reg = Line.requestRegister(line) ?: return emptyList()
        val w = Waiter(reg, line.trimStart().startsWith("set"))
        waiters.add(w)
        try {
            if (!send((line.trimEnd() + "\r\n").toByteArray(Charsets.ISO_8859_1))) return emptyList()
            if (w.wild) {
                val start = System.currentTimeMillis()
                while (true) {
                    delay(20)
                    val now = System.currentTimeMillis()
                    val n = synchronized(w.lines) { w.lines.size }
                    if (n == 0 && now - start > timeoutMs) break
                    if (n > 0 && now - w.last > 250) break
                }
            } else withTimeoutOrNull(timeoutMs) { w.done.await() }
        } finally {
            waiters.remove(w)
        }
        return synchronized(w.lines) { w.lines.toList() }
    }

    // --- connection ---------------------------------------------------------------------------------
    /** looks for the Forge and connects (without asking for the permission: the activities do that) */
    fun connect() {
        scope.launch {
            connectLock.withLock {
                if (link != null && status == Status.ONLINE) return@withLock
                val driver = UsbLink.find(this@ForgeService)
                if (driver == null) { setStatus(Status.SEARCHING); return@withLock }
                val um = getSystemService(Context.USB_SERVICE) as UsbManager
                if (!um.hasPermission(driver.device)) { setStatus(Status.NO_PERMISSION); return@withLock }
                setStatus(Status.CONNECTING)
                if (link == null) {
                    link = runCatching {
                        UsbLink.open(this@ForgeService, driver, 38400, onData = ::onBytes, onGone = ::onGone)
                    }.getOrNull()
                    if (link == null) { setStatus(Status.NO_ANSWER); return@withLock }
                }
                // #41: the GUI talks to the bootloader (firmware update): no handshake, no rate
                // change (it would spoil the rate the bootloader measured); the port stays at 38400
                if (quiet) { setStatus(Status.CONNECTING); return@withLock }
                handshake()
            }
        }
    }

    private suspend fun ping() = request("get.1 system.ping", 600).any { it.kind == '<' }

    /** the GUI lost the line (gui-ng#42): the service finds the rate again; it alone switches the rate
     *  in the app (the GUI switching it as well while the service connected left both sides apart) */
    fun recover() {
        scope.launch {
            connectLock.withLock {
                if (link == null || quiet) return@withLock
                setStatus(Status.CONNECTING)
                handshake()
            }
        }
    }

    private suspend fun handshake() {
        val l = link ?: return
        android.util.Log.i("ForgeSvc", "handshake at ${l.baud} quiet=$quiet")
        send("\r\n".toByteArray())
        // the Forge may be at the fast rate of an earlier session (back to 38400 after 30 s)
        var ok = ping()
        if (!ok) { l.setBaud(FAST); ok = ping() }
        if (!ok) { l.setBaud(38400); ok = ping() }
        if (!ok) { setStatus(Status.NO_ANSWER); return }
        if (l.baud != FAST && request("set.1 system.baud.usb=${FAST / 1200}", 1000).any { it.kind == '*' }) {
            delay(30)
            l.setBaud(FAST)
            if (!ping()) l.setBaud(38400)
        }
        readMaster()
        foreground()
        setStatus(Status.ONLINE)
        startKeepalive()
        volume?.onConnected()
    }

    /** the volume block and its value (control blocks can change: read again on demand) */
    suspend fun readMaster() {
        val blocks = request("get.1 modc.*", 2500).mapNotNull { Line.modc(it) }
        master = masterEvents(blocks)
        masterValue = master?.firstOrNull()?.let { e -> request("get.1 event.$e.value").firstOrNull { it.kind == '<' }?.value?.trim()?.toIntOrNull() }
        notifyState()
    }

    private fun startKeepalive() {
        keepalive?.cancel()
        keepalive = scope.launch {
            var misses = 0
            while (isActive) {
                delay(10_000)
                // the GUI polls by itself; the fast rate falls back after 30 s without a line
                if (gui != null || quiet || System.currentTimeMillis() - lastLine < 8000) { misses = 0; continue }
                if (ping()) misses = 0 else if (++misses >= 2) { setStatus(Status.CONNECTING); connectLock.withLock { handshake() }; misses = 0 }
            }
        }
    }

    private fun onGone() {
        android.util.Log.i("ForgeSvc", "gone")
        link?.close()
        link = null
        keepalive?.cancel()
        setStatus(Status.SEARCHING)
        guiGone?.invoke()
    }

    // --- master volume ------------------------------------------------------------------------------
    private var masterJob: Job? = null
    @Volatile private var masterWant: Int? = null

    /** sets the master (event value 0..255); the latest value wins, at most one write per 60 ms */
    fun setMaster(value: Int) {
        if (master == null) return
        masterWant = value.coerceIn(0, 255)
        if (masterJob?.isActive == true) return
        masterJob = scope.launch {
            while (true) {
                val v = masterWant ?: break
                masterWant = null
                for (e in master ?: break) request("set.1 event.$e.value=$v", 800)
                masterValue = v
                notifyState()
                delay(60)
            }
        }
    }

    // --- GUI ------------------------------------------------------------------------------------------
    /** the bridge of the activity the GUI runs in; only it detaches again (gui-ng#42: after the USB
     *  permission dialog Android recreates the activity, the old one attached late or detached in its
     *  onDestroy and took the lines away from the new page) */
    @Volatile private var guiOwner: Any? = null
    fun attachGui(owner: Any, onData: (ByteArray) -> Unit, onGone: () -> Unit) {
        android.util.Log.i("ForgeSvc", "attach gui ${owner.hashCode()}")
        guiOwner = owner
        gui = onData
        guiGone = onGone
        quiet = false
        connect()
    }
    fun detachGui(owner: Any) {
        if (guiOwner !== owner) return
        android.util.Log.i("ForgeSvc", "detach gui ${owner.hashCode()}")
        guiOwner = null
        gui = null
        guiGone = null
        quiet = false
        lastLine = 0
    }
    /** a line of the GUI; set…system.boot starts the bootloader: quiet until the next ordinary line.
     *  #41: a line while the bootloader is being talked to (raw bytes in the last 3 s, e.g. a rate check
     *  of the GUI) keeps it quiet, else the binary answers of the bootloader went into the line parser
     *  and the update stopped ("bootloader no longer answers"). */
    fun guiWrite(data: ByteArray) {
        val text = String(data, Charsets.ISO_8859_1)
        val wasQuiet = quiet
        val boot = Regex("""^\s*set\S*\s+system\.boot""").containsMatchIn(text)
        quiet = boot || (quiet && System.currentTimeMillis() - lastRaw < 3000)
        if (quiet != wasQuiet) android.util.Log.i("ForgeSvc", "quiet $quiet (line)")
        send(data)
    }
    @Volatile private var lastRaw = 0L
    /** raw bytes of the GUI (bootloader): the service keeps quiet */
    fun guiWriteRaw(data: ByteArray) {
        if (!quiet) android.util.Log.i("ForgeSvc", "quiet true (raw ${data.size} bytes)")
        quiet = true
        lastRaw = System.currentTimeMillis()
        send(data)
    }
    fun setBaud(baud: Int) { runCatching { link?.setBaud(baud) } }

    // --- settings, state ------------------------------------------------------------------------------
    fun updateSettings(s: Settings) {
        settings = s
        Settings.save(this, s)
        volume?.apply(s)
    }

    fun onStatus(fn: (Status) -> Unit) { statusListeners.add(fn); fn(status) }
    /** the GUI talks through the service right now (gui-ng#34: reconnect it when the Forge comes back) */
    val guiAttached get() = gui != null
    fun offStatus(fn: (Status) -> Unit) { statusListeners.remove(fn) }

    private fun setStatus(s: Status) {
        android.util.Log.i("ForgeSvc", "status $s quiet=$quiet gui=${gui != null}")
        status = s
        if (s != Status.ONLINE) volume?.onOffline()
        statusListeners.forEach { it(s) }
        notifyState()
    }

    /** absolute volume and the like for the GUI */
    val absolute get() = volume?.absolute
    fun stateChanged() = notifyState()

    private fun notifyState() {
        if (!inForeground) return
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTE_ID, note())
    }

    private fun note(): Notification {
        val text = when (status) {
            Status.ONLINE -> masterValue?.takeIf { master != null }?.let {
                getString(R.string.note_online_master, if (it == 0) "−∞" else String.format(Locale.getDefault(), "%.1f", masterDb(it)))
            } ?: getString(R.string.note_online)
            Status.CONNECTING -> getString(R.string.note_connecting)
            Status.NO_PERMISSION -> getString(R.string.note_no_permission)
            Status.NO_ANSWER -> getString(R.string.note_no_answer)
            Status.SEARCHING -> getString(R.string.note_searching)
        }
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else @Suppress("DEPRECATION") Notification.Builder(this)
        return b.setSmallIcon(R.drawable.ic_note).setContentTitle(getString(R.string.app_name)).setContentText(text)
            .setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true).build()
    }

    // --- lifecycle --------------------------------------------------------------------------------------
    @Volatile private var inForeground = false

    /** foreground with the notification; from Android 14 the type connectedDevice needs the USB
     * permission first: until then the service runs bound to the GUI and comes forward later */
    private fun foreground() {
        if (inForeground) return
        inForeground = runCatching {
            if (Build.VERSION.SDK_INT >= 29) startForeground(NOTE_ID, note(), ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
            else startForeground(NOTE_ID, note())
        }.isSuccess
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        settings = Settings.load(this)
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.note_channel), NotificationManager.IMPORTANCE_LOW))
        }
        foreground()
        volume = VolumeControl(this).also { it.apply(settings) }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        connect()
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onDestroy() {
        instance = null
        volume?.stop()
        scope.cancel()
        link?.close()
        link = null
        super.onDestroy()
    }

    companion object {
        const val FAST = 460800
        private const val CHANNEL = "forge"
        private const val NOTE_ID = 1
        /** the running service, for the receivers */
        @Volatile var instance: ForgeService? = null
            private set

        /** a Forge with USB permission is plugged in */
        fun permitted(ctx: Context): Boolean {
            val d = UsbLink.find(ctx)?.device ?: return false
            return (ctx.getSystemService(Context.USB_SERVICE) as UsbManager).hasPermission(d)
        }

        /** starts the service in the foreground; only with the USB permission (Android 14+ needs it for
         * the type connectedDevice, and a foreground start must reach startForeground) */
        fun start(ctx: Context): Boolean {
            if (!permitted(ctx)) return false
            val i = Intent(ctx, ForgeService::class.java)
            return runCatching { if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i) }.isSuccess
        }
    }
}
