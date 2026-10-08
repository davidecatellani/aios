package org.aios.nova

import android.app.Application
import org.aios.nova.energy.EnergyJobs
import org.aios.nova.nearby.Nearby

/** Avvio dei lavori vincolati da Android e della ricerca del PC.
 * Il servizio microfono software viene avviato soltanto dalle impostazioni dell'utente. */
class NovaApp : Application() {
    override fun onCreate() {
        super.onCreate()
        EnergyJobs.schedule(this)
        Nearby.start(this)  // scansione BLE filtrata quando il PC è abbinato
    }
}
