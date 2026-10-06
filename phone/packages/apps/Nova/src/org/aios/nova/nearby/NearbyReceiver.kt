package org.aios.nova.nearby

import android.bluetooth.le.BluetoothLeScanner
import android.bluetooth.le.ScanResult
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.aios.nova.pc.PcBridge

/** Risultati della ricerca BLE (svegliano Nova solo per i codici SoIA): è il mio PC? cosa chiede? */
class NearbyReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val secret = PcBridge(context).nearbySecret() ?: return
        @Suppress("DEPRECATION")
        val results: List<ScanResult> = intent.getParcelableArrayListExtra(BluetoothLeScanner.EXTRA_LIST_SCAN_RESULT)
            ?: return
        val lost = intent.getIntExtra(BluetoothLeScanner.EXTRA_CALLBACK_TYPE, 0) ==
            android.bluetooth.le.ScanSettings.CALLBACK_TYPE_MATCH_LOST
        val now = System.currentTimeMillis() / 1000
        for (r in results) {
            val data = r.scanRecord?.getManufacturerSpecificData(NearbyCode.COMPANY) ?: continue
            val flags = NearbyCode.readPc(data, secret, now) ?: continue
            context.startForegroundService(Intent(context, NearbyService::class.java)
                .putExtra(NearbyService.EXTRA_FLAGS, flags).putExtra(NearbyService.EXTRA_LOST, lost))
            return
        }
    }
}
