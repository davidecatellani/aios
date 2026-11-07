package org.aios.nova.energy

import android.app.job.JobParameters
import android.app.job.JobService
import android.util.Log
import org.aios.nova.llm.LocalModel

/** Esegue i lavori pianificati da EnergyJobs, in un thread, e libera subito il sistema. */
class EnergyJobService : JobService() {
    private val workers=java.util.concurrent.ConcurrentHashMap<Int,Thread>()
    override fun onStartJob(params: JobParameters): Boolean {
        val worker=Thread {
            try {
                when (params.jobId) {
                    EnergyJobs.LIGHT -> {
                        org.aios.nova.core.PrototypeStore(applicationContext).sync()
                        org.aios.nova.core.Reminders.schedule(applicationContext)
                        org.aios.nova.nearby.Nearby.start(applicationContext)  // se il Bluetooth era spento
                    }
                    EnergyJobs.HEAVY -> LocalModel(applicationContext).downloadIfMissing()
                }
            } catch (e: Exception) {
                Log.w(TAG, "lavoro ${params.jobId} non riuscito: ${e.message}")
            } finally {
                if(workers.remove(params.jobId)===Thread.currentThread()) jobFinished(params, false)
            }
        }
        workers[params.jobId]=worker; worker.start()
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean { workers.remove(params.jobId)?.interrupt(); return true }
    override fun onDestroy() { workers.values.forEach { it.interrupt() }; workers.clear(); super.onDestroy() }

    companion object { const val TAG = "NovaEnergia" }
}
