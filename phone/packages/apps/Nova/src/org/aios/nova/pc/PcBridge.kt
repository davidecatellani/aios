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
class PcBridge(context: Context) {
    private val prefs = context.getSharedPreferences("pc", Context.MODE_PRIVATE)

    fun isPaired(): Boolean = prefs.getString("key", null) != null

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

    private fun request(base: String, fingerprint: String, key: String?, method: String, path: String,
                        body: JSONObject?): JSONObject {
        val conn = URL(base + path).openConnection() as HttpsURLConnection
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
