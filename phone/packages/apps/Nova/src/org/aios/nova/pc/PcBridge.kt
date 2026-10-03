package org.aios.nova.pc

import android.content.Context
import android.net.Uri
import android.os.Build
import org.json.JSONObject
import java.net.URL
import java.security.MessageDigest
import java.security.cert.X509Certificate
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/**
 * Collegamento con il PC (aios-telefono sul PC, mesh/files.py e mesh/delegate.py).
 * HTTPS con il certificato del PC «fissato»: si accetta solo l'impronta ricevuta con il QR,
 * quindi nessun altro può fingersi il PC.
 */
class PcBridge(private val context: Context) {
    companion object {
        @Volatile private var linkNetwork: android.net.Network? = null
        @Volatile private var linkHost: String? = null

        /** Collegamento diretto con il PC (NearbyService): rete da usare e indirizzo del PC. */
        fun link(network: android.net.Network?, host: String?) {
            linkNetwork = network
            linkHost = host
        }
    }

    private val prefs = context.getSharedPreferences("pc", Context.MODE_PRIVATE)

    fun isPaired(): Boolean = prefs.getString("key", null) != null

    /** Il segreto con cui telefono e PC si riconoscono via Bluetooth (nearby/NearbyCode.kt). */
    fun nearbySecret(): ByteArray? = prefs.getString("key", null)?.let { org.aios.nova.nearby.NearbyCode.secretFromKey(it) }

    fun port(): Int? = prefs.getString("url", null)?.let { Uri.parse(it).port }?.takeIf { it > 0 }

    /** È davvero il mio PC? (la richiesta riesce solo con il certificato dall'impronta giusta e la chiave valida) */
    fun check(): Boolean = try {
        post("/api/vicino", JSONObject().put("bt", bluetoothAddress() ?: ""))
        true
    } catch (e: Exception) {
        false
    }

    /** Richiesta firmata generica verso il PC (per esempio /api/vicino). */
    fun post(path: String, body: JSONObject): JSONObject =
        request(prefs.getString("url", null)!!, prefs.getString("fp", null)!!, prefs.getString("key", null), "POST", path, body)

    /** Dal QR del PC: https://ip:porta/#abbina=CODICE&fp=IMPRONTA */
    fun pair(qr: String): String {
        val uri = Uri.parse(qr)
        val fragment = (uri.fragment ?: "").split("&").mapNotNull {
            val parts = it.split("=", limit = 2)
            if (parts.size == 2) parts[0] to parts[1] else null
        }.toMap()
        val code = fragment["abbina"] ?: throw IllegalArgumentException("codice QR non valido")
        val fp = fragment["fp"] ?: throw IllegalArgumentException("nel codice QR manca l'impronta del PC")
        val base = "https://${uri.host}:${uri.port}"
        val body = JSONObject().put("code", code).put("name", Build.MODEL).put("kind", "telefono")
        bluetoothAddress()?.let { body.put("bt", it) }  // per collegarsi anche senza Wi-Fi
        val reply = request(base, fp, null, "POST", "/api/abbina", body)
        prefs.edit().putString("url", base).putString("fp", fp).putString("key", reply.getString("key"))
            .putString("pc", reply.optString("pc", "PC")).apply()
        return reply.optString("pc", "PC")
    }

    /** «Chiedi al PC»: il copilota del PC risponde, con strumenti limitati e conferme sul telefono. */
    fun ask(text: String, timeoutMs: Long = 120_000): String {
        val url = prefs.getString("url", null)!!
        val fp = prefs.getString("fp", null)!!
        val key = prefs.getString("key", null)!!
        val job = request(url, fp, key, "POST", "/api/chiedi", JSONObject().put("text", text)).getString("job")
        val deadline = System.currentTimeMillis() + timeoutMs
        var after = 0
        while (System.currentTimeMillis() < deadline) {
            val state = request(url, fp, key, "GET", "/api/job/$job?after=$after", null)
            after = state.optInt("next", after)
            if (state.optBoolean("done")) return state.optString("answer")
            Thread.sleep(700)
        }
        return "Il computer non ha risposto in tempo."
    }

    private fun bluetoothAddress(): String? = try {
        context.getSystemService(android.bluetooth.BluetoothManager::class.java)?.adapter?.address
    } catch (e: SecurityException) {
        null
    }

    private fun request(base: String, fingerprint: String, key: String?, method: String, path: String,
                        body: JSONObject?): JSONObject {
        // Senza Wi-Fi in comune: attraverso la rete diretta (o Bluetooth) aperta con il PC. L'indirizzo
        // cambia, l'identità no: l'impronta del certificato resta la stessa.
        val net = linkNetwork
        val host = linkHost
        val target = if (host != null) Uri.parse(base).buildUpon().encodedAuthority("$host:${Uri.parse(base).port}").build().toString() else base
        val url = URL(target + path)
        val conn = (if (net != null) net.openConnection(url) else url.openConnection()) as HttpsURLConnection
        conn.sslSocketFactory = pinnedContext(fingerprint).socketFactory
        conn.setHostnameVerifier { _, _ -> true }  // l'identità del PC la garantisce l'impronta, non il nome
        conn.requestMethod = method
        conn.connectTimeout = 3_000
        conn.readTimeout = 60_000
        conn.setRequestProperty("Content-Type", "application/json")
        if (key != null) conn.setRequestProperty("Authorization", "Bearer $key")
        if (body != null) {
            conn.doOutput = true
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        val stream = if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream
        val text = stream.bufferedReader().use { it.readText() }
        val json = JSONObject(text.ifEmpty { "{}" })
        if (conn.responseCode !in 200..299) throw IllegalStateException(json.optString("error", "errore ${conn.responseCode}"))
        return json
    }

    private fun pinnedContext(fingerprint: String): SSLContext {
        val trust = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
                throw java.security.cert.CertificateException("non usato")

            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                val digest = MessageDigest.getInstance("SHA-256").digest(chain[0].encoded)
                val hex = digest.joinToString("") { "%02x".format(it) }
                if (!hex.equals(fingerprint, ignoreCase = true)) {
                    throw java.security.cert.CertificateException("il certificato non è quello del tuo PC")
                }
            }

            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        }
        return SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), null) }
    }
}
