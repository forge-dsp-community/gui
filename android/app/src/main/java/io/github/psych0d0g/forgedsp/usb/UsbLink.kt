package io.github.psych0d0g.forgedsp.usb

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import com.hoho.android.usbserial.driver.UsbSerialDriver
import com.hoho.android.usbserial.driver.UsbSerialPort
import com.hoho.android.usbserial.driver.UsbSerialProber
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/**
 * The USB-serial bridge of the Forge (CP2102) through the Android USB host API. Received bytes go to
 * [onData] from the reader thread as they come (the GUI splits the lines, the bootloader wants raw
 * bytes). 8N1 at 38400 Bd, DTR on (the firmware sends its events to the USB port only with DTR, as
 * a terminal or the web GUI holds it).
 */
class UsbLink private constructor(private val port: UsbSerialPort, private val onData: (ByteArray) -> Unit, private val onGone: () -> Unit) {
    @Volatile private var open = true
    private val reader = Thread({ readLoop() }, "forge-usb-read").apply { isDaemon = true }

    var baud = 38400
        private set

    fun setBaud(b: Int) {
        android.util.Log.i("ForgeLink", "baud $b")
        port.setParameters(b, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE)
        baud = b
    }

    /** blocking; called from the JavaScript bridge thread */
    fun write(data: ByteArray) = port.write(data, 2000)

    private fun readLoop() {
        val buf = ByteArray(4096)
        try {
            while (open) {
                val n = port.read(buf, 100)
                if (n > 0) onData(buf.copyOf(n))
            }
        } catch (_: Exception) {
            if (open) onGone()   // unplugged or the port failed
        }
    }

    fun close() {
        open = false
        runCatching { port.close() }
    }

    companion object {
        private const val ACTION = "io.github.psych0d0g.forgedsp.USB_PERMISSION"

        /** the first USB-serial device that a driver knows (the Forge: CP2102) */
        fun find(ctx: Context): UsbSerialDriver? {
            val um = ctx.getSystemService(Context.USB_SERVICE) as UsbManager
            return UsbSerialProber.getDefaultProber().findAllDrivers(um).firstOrNull()
        }

        /** asks for the permission if needed; false = refused */
        suspend fun permit(ctx: Context, dev: UsbDevice): Boolean {
            val um = ctx.getSystemService(Context.USB_SERVICE) as UsbManager
            if (um.hasPermission(dev)) return true
            return suspendCancellableCoroutine { cont ->
                val rx = object : BroadcastReceiver() {
                    override fun onReceive(c: Context, i: Intent) {
                        if (i.action != ACTION) return
                        runCatching { ctx.unregisterReceiver(this) }
                        if (cont.isActive) cont.resume(i.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false) || um.hasPermission(dev))
                    }
                }
                val filter = IntentFilter(ACTION)
                if (Build.VERSION.SDK_INT >= 33) ctx.registerReceiver(rx, filter, Context.RECEIVER_NOT_EXPORTED)
                else @Suppress("UnspecifiedRegisterReceiverFlag") ctx.registerReceiver(rx, filter)
                cont.invokeOnCancellation { runCatching { ctx.unregisterReceiver(rx) } }
                val flags = if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
                val pi = PendingIntent.getBroadcast(ctx, 0, Intent(ACTION).setPackage(ctx.packageName), flags)
                um.requestPermission(dev, pi)
            }
        }

        fun open(ctx: Context, driver: UsbSerialDriver, baud: Int, onData: (ByteArray) -> Unit, onGone: () -> Unit): UsbLink {
            val um = ctx.getSystemService(Context.USB_SERVICE) as UsbManager
            val conn = um.openDevice(driver.device) ?: throw IllegalStateException("USB device could not be opened")
            val port = driver.ports[0]
            port.open(conn)
            port.setParameters(baud, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE)
            runCatching { port.dtr = true; port.rts = true }  // as pyserial and Web Serial on open
            return UsbLink(port, onData, onGone).also { it.baud = baud; it.reader.start() }
        }
    }
}
