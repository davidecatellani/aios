package org.aios.nova.assistant

import android.app.Service
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.os.IBinder
import android.os.BatteryManager
import org.aios.nova.NovaActivity
import java.util.UUID

/** Modalità software facoltativa, avviata dall'utente e sempre visibile. Nessuna dipendenza dal DSP. */
class WakeService:Service() {
    private val voice by lazy { OfflineVoice(applicationContext) }
    @Volatile private var running=false
    private var worker:Thread?=null
    override fun onBind(intent:Intent?):IBinder?=null
    override fun onStartCommand(intent:Intent?,flags:Int,startId:Int):Int {
        if(intent?.action=="stop") { stopSelf(); return START_NOT_STICKY }
        val notifications=getSystemService(NotificationManager::class.java) ?: return START_NOT_STICKY
        notifications.createNotificationChannel(NotificationChannel("wake","Ascolto Ehi Nova",NotificationManager.IMPORTANCE_LOW))
        val stop=PendingIntent.getService(this,1,Intent(this,WakeService::class.java).setAction("stop"),PendingIntent.FLAG_IMMUTABLE)
        startForeground(41,Notification.Builder(this,"wake").setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Ehi Nova attivo — microfono offline").setContentText("Modalità software: tocca Arresta per interrompere")
            .addAction(Notification.Action.Builder(null,"Arresta",stop).build()).setOngoing(true).build())
        if(running) return START_NOT_STICKY
        running=true
        worker=Thread {
            try {
                while(running && !Thread.currentThread().isInterrupted) {
                    val battery=getSystemService(BatteryManager::class.java)?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) ?: -1
                    if(battery in 0..14) break
                    if(paused) { Thread.sleep(500); continue }
                    val text=voice.listen(6)
                    val match=Regex("(?i)\\b(?:ehi|hey|ei)\\s+nova\\b[,:.!? ]*").find(text) ?: continue
                    val command=text.substring(match.range.last+1).trim().ifBlank { voice.listen() }
                    if(command.isBlank()) continue
                    val token=UUID.randomUUID().toString()
                    synchronized(COMMANDS) { COMMANDS.clear(); COMMANDS[token]=command }
                    val open=Intent(this,NovaActivity::class.java).putExtra("wake_token",token)
                    val pending=PendingIntent.getActivity(this,42,open,PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
                    notifications.notify(42,Notification.Builder(this,"wake").setSmallIcon(android.R.drawable.ic_btn_speak_now)
                        .setContentTitle("Nova ti ha ascoltato").setContentText(command.take(100)).setContentIntent(pending).setAutoCancel(true).build())
                    val active=NovaInteractionService.active
                    if(active!=null) runCatching { active.showSession(android.os.Bundle().apply { putString("wake_token",token) },0) }
                    Thread.sleep(1000)
                }
            } catch(_:Exception) { }
            finally { running=false; stopSelf() }
        }.also { it.start() }
        return START_NOT_STICKY
    }
    override fun onDestroy() { running=false; voice.cancel(); worker?.interrupt(); stopForeground(STOP_FOREGROUND_REMOVE); super.onDestroy() }
    companion object {
        @Volatile var paused=false
        private val COMMANDS=mutableMapOf<String,String>()
        fun consume(intent:Intent?):String?=synchronized(COMMANDS) { COMMANDS.remove(intent?.getStringExtra("wake_token")) }
    }
}
