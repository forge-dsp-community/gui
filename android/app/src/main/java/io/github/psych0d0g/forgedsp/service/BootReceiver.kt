package io.github.psych0d0g.forgedsp.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** with the head unit: the service, if the Forge is there and the USB permission was given "by default" */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        if (Settings.load(ctx).autostart) ForgeService.start(ctx)
    }
}
