package io.github.psych0d0g.forgedsp.service

import android.app.Activity
import android.os.Bundle

/**
 * Receives USB_DEVICE_ATTACHED for the Forge (only activities can): Android grants the USB
 * permission with it (with "use by default" for good). Starts the service and closes at once, so
 * plugging in does not open the GUI.
 */
class UsbAttachActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ForgeService.start(this)
        ForgeService.instance?.connect()
        finish()
    }
}
