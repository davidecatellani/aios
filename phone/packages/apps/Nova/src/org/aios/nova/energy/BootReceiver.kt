package org.aios.nova.energy

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            EnergyJobs.schedule(context)
            val result=goAsync()
            Thread { try { org.aios.nova.core.Reminders.schedule(context) } finally { result.finish() } }.start()
        }
    }
}
