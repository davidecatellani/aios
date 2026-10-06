package org.aios.nova.nearby

import android.app.PendingIntent
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.Intent
import android.util.Log
import org.aios.nova.pc.PcBridge

/**
 * Il telefono ascolta, il PC si annuncia: la ricerca BLE è affidata al chip Bluetooth con un filtro
 * (solo i dati del produttore SoIA) e sveglia Nova solo quando c'è un risultato. Così ascoltare costa
 * quasi nulla e non serve alcun servizio sempre acceso; NearbyService parte solo con il PC vicino.
 */
object Nearby {
    private const val TAG = "NovaVicino"

    fun start(context: Context) {
        if (!PcBridge(context).isPaired()) return
        val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter ?: return
        val scanner = adapter.bluetoothLeScanner ?: return  // Bluetooth spento: riproviamo all'accensione
        val filter = ScanFilter.Builder().setManufacturerData(NearbyCode.COMPANY, byteArrayOf(NearbyCode.VERSION.toByte()),
            byteArrayOf(0xFF.toByte())).build()
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_POWER)
            .setCallbackType(ScanSettings.CALLBACK_TYPE_FIRST_MATCH or ScanSettings.CALLBACK_TYPE_MATCH_LOST)
            .setReportDelay(0)
            .build()
        try {
            scanner.startScan(listOf(filter), settings, pending(context))
        } catch (e: SecurityException) {
            Log.w(TAG, "permessi Bluetooth mancanti: ${e.message}")
        }
    }

    fun pending(context: Context): PendingIntent = PendingIntent.getBroadcast(
        context, 0, Intent(context, NearbyReceiver::class.java),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE)

    /** Comunica al PC l'indirizzo Bluetooth del telefono (per la rete Bluetooth senza Wi-Fi). */
    fun reportBluetooth(context: Context) {
        try {
            val address = context.getSystemService(BluetoothManager::class.java)?.adapter?.address ?: return
            PcBridge(context).post("/api/vicino", org.json.JSONObject().put("bt", address))
        } catch (e: Exception) {
            Log.i(TAG, "indirizzo Bluetooth non inviato: ${e.message}")
        }
    }
}
