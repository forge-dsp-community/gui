package io.github.psych0d0g.forgedsp.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Lines of other apps (#29), e.g. Tasker or a car integration. Needs the permission
 * io.github.psych0d0g.forgedsp.permission.CONTROL (the user grants it to that app):
 *
 *   send:    action io.github.psych0d0g.forgedsp.action.SEND, package io.github.psych0d0g.forgedsp,
 *            extra "line" = "get.1 system.temperature.celsius" / "set.1 mute.out.1=1", optional "id"
 *   answer:  action ….action.ANSWER, extras "id", "lines" (String[], the answer lines; empty = none,
 *            "error" = why not sent)
 *   events:  action ….action.EVENT, extra "line" (every #-line of the Forge, e.g. preset.loaded)
 *
 * Answers and events are sent only to apps that hold the permission.
 */
class CommandReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != ACTION_SEND) return
        val line = intent.getStringExtra("line")?.trim().orEmpty()
        val id = intent.getStringExtra("id")
        val svc = ForgeService.instance
        val err = when {
            svc == null -> "service not running"
            Line.requestRegister(line) == null || line.any { it == '\r' || it == '\n' } -> "not a request line"
            svc.quiet -> "busy (firmware update)"
            svc.status != ForgeService.Status.ONLINE -> "not connected"
            else -> null
        }
        if (err != null || svc == null) { answer(ctx, id, emptyList(), err ?: "service not running"); return }
        val done = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                answer(ctx, id, svc.request(line, 3000, fromOutside = true).map { it.raw }, null)
            } finally {
                done.finish()
            }
        }
    }

    companion object {
        const val PERMISSION = "io.github.psych0d0g.forgedsp.permission.CONTROL"
        const val ACTION_SEND = "io.github.psych0d0g.forgedsp.action.SEND"
        const val ACTION_ANSWER = "io.github.psych0d0g.forgedsp.action.ANSWER"
        const val ACTION_EVENT = "io.github.psych0d0g.forgedsp.action.EVENT"

        private fun answer(ctx: Context, id: String?, lines: List<String>, error: String?) {
            val i = Intent(ACTION_ANSWER).putExtra("id", id).putExtra("lines", lines.toTypedArray())
            if (error != null) i.putExtra("error", error)
            ctx.sendBroadcast(i, PERMISSION)
        }

        fun event(ctx: Context, line: String) = ctx.sendBroadcast(Intent(ACTION_EVENT).putExtra("line", line), PERMISSION)
    }
}
