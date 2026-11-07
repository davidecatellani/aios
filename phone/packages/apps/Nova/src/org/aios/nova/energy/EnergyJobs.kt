package org.aios.nova.energy

import android.app.job.JobInfo
import android.app.job.JobScheduler
import android.content.ComponentName
import android.content.Context

/**
 * Tutto il lavoro in sottofondo di Nova passa da qui, con i vincoli di Android:
 * - leggero (sincronizzazione con il PC o il relay): ogni 30 minuti al massimo, mai con batteria bassa;
 *   Android lo raggruppa con gli altri risvegli (Doze);
 * - pesante (foto, indice dei file, scaricamento del modello AI): solo in carica, su Wi-Fi e a telefono fermo.
 * Le decisioni più fini (abitudini di ricarica) arriveranno portando qui energy.py.
 */
object EnergyJobs {
    const val LIGHT = 1
    const val HEAVY = 2

    fun schedule(context: Context) {
        val scheduler = context.getSystemService(JobScheduler::class.java) ?: return
        val service = ComponentName(context, EnergyJobService::class.java)
        if (scheduler.getPendingJob(LIGHT) == null) {
            scheduler.schedule(JobInfo.Builder(LIGHT, service)
                .setPeriodic(30 * 60 * 1000L, 10 * 60 * 1000L)
                .setRequiresBatteryNotLow(true)
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPersisted(true)
                .build())
        }
        if (scheduler.getPendingJob(HEAVY) == null) {
            scheduler.schedule(JobInfo.Builder(HEAVY, service)
                .setPeriodic(6 * 60 * 60 * 1000L)
                .setRequiresCharging(true)
                .setRequiresDeviceIdle(true)
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_UNMETERED)
                .setPersisted(true)
                .build())
        }
    }
}
