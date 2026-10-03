package org.aios.nova.energy

import android.app.job.JobParameters
import android.app.job.JobService
import android.util.Log
import org.aios.nova.llm.LocalModel

/** Esegue i lavori pianificati da EnergyJobs, in un thread, e libera subito il sistema. */
class EnergyJobService : JobService() {
    override fun onStartJob(params: JobParameters): Boolean {
        Thread {
            try {
                when (params.jobId) {
                    EnergyJobs.LIGHT -> {
                        Log.i(TAG, "sincronizzazione leggera")  // TODO: /api/sync firmato (identity.py)
                        org.aios.nova.nearby.Nearby.start(applicationContext)  // se il Bluetooth era spento
                    }
                    EnergyJobs.HEAVY -> LocalModel(applicationContext).downloadIfMissing()
                }
            } catch (e: Exception) {
                Log.w(TAG, "lavoro ${params.jobId} non riuscito: ${e.message}")
            } finally {
                jobFinished(params, false)
            }
        }.start()
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean = true  // condizioni cambiate: riprova dopo

    companion object { const val TAG = "NovaEnergia" }
}
