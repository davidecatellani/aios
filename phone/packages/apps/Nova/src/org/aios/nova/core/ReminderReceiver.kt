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

class ReminderReceiver:BroadcastReceiver() {
    override fun onReceive(context:Context,intent:Intent) {
        val manager=context.getSystemService(NotificationManager::class.java) ?: return
        if(!manager.areNotificationsEnabled()) return
        manager.createNotificationChannel(NotificationChannel("agenda","Promemoria Nova",NotificationManager.IMPORTANCE_HIGH))
        if(manager.getNotificationChannel("agenda").importance==NotificationManager.IMPORTANCE_NONE) return
        val store=PrototypeStore(context)
        for((key,value) in store.records("agenda/")) {
            val doc=value as? JSONObject ?: continue
            if(doc.optString("kind")!="reminder" || doc.optBoolean("done")) continue
            val due=runCatching { LocalDateTime.parse(doc.getString("due")).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli() }.getOrNull() ?: continue
            if(due>System.currentTimeMillis()) continue
            val open=PendingIntent.getActivity(context,key.hashCode(),Intent(context,NovaActivity::class.java),PendingIntent.FLAG_IMMUTABLE)
            manager.notify(key.hashCode(),Notification.Builder(context,"agenda").setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(doc.optString("title","Promemoria")).setContentText("Promemoria SoIA").setContentIntent(open).setAutoCancel(true).build())
            doc.put("done",true); store.set(key,doc)
        }
        Reminders.schedule(context)
    }
}
