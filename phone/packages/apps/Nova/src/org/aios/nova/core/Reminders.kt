package org.aios.nova.core

import android.content.Context
import android.content.Intent
import android.content.BroadcastReceiver
import android.app.AlarmManager
import android.app.PendingIntent
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import java.time.LocalDateTime
import java.time.ZoneId
import org.json.JSONObject
import org.aios.nova.NovaActivity

/** Alarmi non esatti: rispettano Doze, senza permesso speciale per sveglie. */
object Reminders {
    fun schedule(context:Context) {
        val alarm=context.getSystemService(AlarmManager::class.java) ?: return
        val pending=PendingIntent.getBroadcast(context,77,Intent(context,ReminderReceiver::class.java),PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        alarm.cancel(pending)
        if(context.getSystemService(NotificationManager::class.java)?.areNotificationsEnabled()!=true) return
        val next=PrototypeStore(context).records("agenda/").values.mapNotNull { value ->
            val doc=value as? JSONObject ?: return@mapNotNull null
            if(doc.optString("kind")!="reminder" || doc.optBoolean("done")) return@mapNotNull null
            runCatching { LocalDateTime.parse(doc.getString("due")).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli() }.getOrNull()
        }.minOrNull() ?: return
        alarm.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP,maxOf(next,System.currentTimeMillis()+2000),pending)
    }
}
