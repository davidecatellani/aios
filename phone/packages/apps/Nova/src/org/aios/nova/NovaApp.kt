package org.aios.nova

import android.app.Application
import org.aios.nova.energy.EnergyJobs
import org.aios.nova.nearby.Nearby

/** Nova sul telefono: niente servizi sempre attivi, solo lavori pianificati con i vincoli di energia
 *  (e il collegamento col PC, acceso solo mentre il PC è vicino). */
class NovaApp : Application() {
    override fun onCreate() {
        super.onCreate()
        EnergyJobs.schedule(this)
        Nearby.start(this)  // ascolto BLE affidato al chip: costa quasi nulla
    }
}
