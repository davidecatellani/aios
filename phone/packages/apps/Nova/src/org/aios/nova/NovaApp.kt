package org.aios.nova

import android.app.Application
import org.aios.nova.energy.EnergyJobs

/** Nova sul telefono: niente servizi sempre attivi, solo lavori pianificati con i vincoli di energia. */
class NovaApp : Application() {
    override fun onCreate() {
        super.onCreate()
        EnergyJobs.schedule(this)
    }
}
