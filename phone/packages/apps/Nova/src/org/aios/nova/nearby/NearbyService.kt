package org.aios.nova.nearby

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.bluetooth.BluetoothManager
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.TetheringManager
import android.net.wifi.SoftApConfiguration
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSpecifier
import android.os.BatteryManager
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import org.aios.nova.pc.PcBridge
import java.util.concurrent.Executors

/**
 * Attivo solo mentre il PC è vicino (con la sua notifica, come vuole Android):
 * - annuncia il codice del telefono (il PC così sa che c'è, se ha internet e quanta batteria);
 * - esegue ciò che il PC chiede nel suo codice firmato: entrare nella rete diretta, aprire la rete
 *   Bluetooth, condividere internet (mai con la batteria bassa);
 * - si spegne da solo pochi minuti dopo che il PC si è allontanato, chiudendo ciò che ha aperto.
 */
class NearbyService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor()
    private var lastSeen = 0L
    private var pcFlags = 0
    private var joined: ConnectivityManager.NetworkCallback? = null
    private var tethering = 0  // TetheringManager.TETHERING_* attivo, -1 nessuno
    private var savedAp: SoftApConfiguration? = null
    private var advertising: AdvertiseCallback? = null
    private var scanning: ScanCallback? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        tethering = -1
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Collegamento con il PC", NotificationManager.IMPORTANCE_MIN))
        startForeground(1, Notification.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentTitle("Collegato al PC").setContentText("Nova tiene il collegamento finché il PC è vicino").build())
        follow()
        main.postDelayed(::check, CHECK_MS)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent != null && !intent.getBooleanExtra(EXTRA_LOST, false)) {
            onPc(intent.getIntExtra(EXTRA_FLAGS, 0))
        }
        return START_NOT_STICKY
    }

    private fun onPc(flags: Int) {
        lastSeen = System.currentTimeMillis()
        val changed = flags != pcFlags
        pcFlags = flags
        advertise()
        if (!changed) return
        val secret = PcBridge(this).nearbySecret() ?: return
        val now = System.currentTimeMillis() / 1000
        when {
            flags and NearbyCode.F_RETE != 0 -> join(NearbyCode.network(secret, now))
            flags and NearbyCode.F_CHIEDE_INTERNET != 0 && !lowBattery() -> tether(TetheringManager.TETHERING_WIFI, NearbyCode.network(secret, now))
            flags and NearbyCode.F_CHIEDE_BT != 0 -> tether(TetheringManager.TETHERING_BLUETOOTH, null)
            else -> closeAll()
        }
    }

    /** Il codice del telefono: c'è internet da condividere? batteria? */
    private fun advertise() {
        val secret = PcBridge(this).nearbySecret() ?: return
        var flags = 0
        val cm = getSystemService(ConnectivityManager::class.java)
        val caps = cm.getNetworkCapabilities(cm.activeNetwork)
        if (caps?.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) == true &&
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) flags = flags or NearbyCode.F_INTERNET
        if (lowBattery()) flags = flags or NearbyCode.F_BATTERIA_BASSA
        if (getSystemService(BatteryManager::class.java).isCharging) flags = flags or NearbyCode.F_IN_CARICA
        val advertiser = getSystemService(BluetoothManager::class.java)?.adapter?.bluetoothLeAdvertiser ?: return
        advertising?.let { advertiser.stopAdvertising(it) }
        val payload = NearbyCode.make(secret, NearbyCode.ROLE_PHONE, flags, System.currentTimeMillis() / 1000)
        val callback = object : AdvertiseCallback() {}
        advertiser.startAdvertising(
            AdvertiseSettings.Builder().setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_POWER)
                .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_LOW).setConnectable(false).build(),
            AdvertiseData.Builder().addManufacturerData(NearbyCode.COMPANY, payload).build(), callback)
        advertising = callback
    }

    /** Mentre il PC è vicino seguiamo i suoi codici (le richieste cambiano), a basso consumo. */
    private fun follow() {
        val scanner = getSystemService(BluetoothManager::class.java)?.adapter?.bluetoothLeScanner ?: return
        val secret = PcBridge(this).nearbySecret() ?: return
        val callback = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                val data = result.scanRecord?.getManufacturerSpecificData(NearbyCode.COMPANY) ?: return
                NearbyCode.readPc(data, secret, System.currentTimeMillis() / 1000)?.let { onPc(it) }
            }
        }
        scanner.startScan(listOf(ScanFilter.Builder().setManufacturerData(NearbyCode.COMPANY,
            byteArrayOf(NearbyCode.VERSION.toByte()), byteArrayOf(0xFF.toByte())).build()),
            ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_POWER).build(), callback)
        scanning = callback
    }

    /** Entra nella rete diretta del PC senza lasciare i dati mobili (la rete serve solo a Nova). */
    private fun join(network: Pair<String, String>) {
        closeAll()
        val spec = WifiNetworkSpecifier.Builder().setSsid(network.first).setWpa2Passphrase(network.second)
            .setIsHiddenSsid(true).build()
        val request = NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET).setNetworkSpecifier(spec).build()
        val cm = getSystemService(ConnectivityManager::class.java)
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(net: Network) {
                val gateway = cm.getLinkProperties(net)?.routes?.firstOrNull { it.hasGateway() }?.gateway?.hostAddress
                PcBridge.link(net, gateway ?: "10.42.0.1")
                Log.i(TAG, "nella rete diretta del PC")
            }

            override fun onLost(net: Network) = PcBridge.link(null, null)
        }
        cm.requestNetwork(request, callback)
        joined = callback
    }

    /** Rete Bluetooth (leggera) o internet del telefono (hotspot con nome e password che conosce solo il PC). */
    private fun tether(type: Int, network: Pair<String, String>?) {
        closeAll()
        val wifi = getSystemService(WifiManager::class.java)
        if (network != null) {
            savedAp = wifi.softApConfiguration
            wifi.setSoftApConfiguration(SoftApConfiguration.Builder().setSsid(network.first)
                .setPassphrase(network.second, SoftApConfiguration.SECURITY_TYPE_WPA2_PSK).setHiddenSsid(true).build())
        }
        val tm = getSystemService(TetheringManager::class.java)
        tm.startTethering(TetheringManager.TetheringRequest.Builder(type).build(), executor,
            object : TetheringManager.StartTetheringCallback {
                override fun onTetheringStarted() {
                    tethering = type
                    findPc()
                }
                override fun onTetheringFailed(error: Int) { Log.w(TAG, "condivisione non riuscita: $error") }
            })
    }

    /**
     * Quando la rete la apre il telefono, il PC riceve un indirizzo che il telefono non conosce: lo cerchiamo
     * nella piccola rete condivisa (porta del PC aperta + certificato con l'impronta giusta, nient'altro vale).
     */
    private fun findPc() {
        executor.execute {
            val bridge = PcBridge(this)
            val port = bridge.port() ?: return@execute
            repeat(10) {
                for (iface in java.net.NetworkInterface.getNetworkInterfaces()) {
                    if (!iface.isUp || iface.isLoopback) continue
                    if (!(iface.name.startsWith("bt-pan") || iface.name.startsWith("ap") || iface.name.startsWith("wlan") ||
                            iface.name.startsWith("swlan"))) continue
                    for (addr in iface.interfaceAddresses) {
                        val v4 = addr.address as? java.net.Inet4Address ?: continue
                        if (addr.networkPrefixLength < 24) continue
                        val base = v4.address
                        for (last in 1..254) {
                            if (last == (base[3].toInt() and 0xFF)) continue
                            val host = "${base[0].toInt() and 0xFF}.${base[1].toInt() and 0xFF}.${base[2].toInt() and 0xFF}.$last"
                            if (!open(host, port)) continue
                            PcBridge.link(null, host)
                            if (bridge.check()) {
                                Log.i(TAG, "PC trovato nella rete condivisa")
                                return@execute
                            }
                            PcBridge.link(null, null)
                        }
                    }
                }
                Thread.sleep(3_000)  // il PC potrebbe non essere ancora entrato
            }
        }
    }

    private fun open(host: String, port: Int): Boolean = try {
        java.net.Socket().use { it.connect(java.net.InetSocketAddress(host, port), 150); true }
    } catch (e: java.io.IOException) {
        false
    }

    private fun closeAll() {
        joined?.let { getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(it) }
        joined = null
        PcBridge.link(null, null)
        if (tethering >= 0) getSystemService(TetheringManager::class.java).stopTethering(tethering)
        tethering = -1
        savedAp?.let { getSystemService(WifiManager::class.java).setSoftApConfiguration(it) }
        savedAp = null
    }

    private fun lowBattery(): Boolean {
        val bm = getSystemService(BatteryManager::class.java)
        return bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) < 20 && !bm.isCharging
    }

    private fun check() {
        if (System.currentTimeMillis() - lastSeen > GONE_MS) {
            stopSelf()
            return
        }
        advertise()  // il codice cambia ogni 15 minuti
        main.postDelayed(::check, CHECK_MS)
    }

    override fun onDestroy() {
        closeAll()
        val adapter = getSystemService(BluetoothManager::class.java)?.adapter
        advertising?.let { adapter?.bluetoothLeAdvertiser?.stopAdvertising(it) }
        scanning?.let { adapter?.bluetoothLeScanner?.stopScan(it) }
        main.removeCallbacksAndMessages(null)
        executor.shutdown()
        super.onDestroy()
    }

    companion object {
        const val EXTRA_FLAGS = "flags"
        const val EXTRA_LOST = "lost"
        private const val CHANNEL = "vicino"
        private const val TAG = "NovaVicino"
        private const val CHECK_MS = 60_000L
        private const val GONE_MS = 3 * 60_000L
    }
}
