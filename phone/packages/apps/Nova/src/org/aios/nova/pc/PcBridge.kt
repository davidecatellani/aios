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
import org.aios.nova.core.PhoneIdentity
import org.aios.nova.core.Secrets
import org.aios.nova.core.VerifiedFiles
import java.io.File
import java.io.InputStream
import org.json.JSONArray

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
    private val secrets = Secrets(context)
    init {
        prefs.getString("key", null)?.let { legacy ->
            if (secrets.get("pc.key") == null) secrets.set("pc.key", legacy.toByteArray())
            prefs.edit().remove("key").commit()
        }
    }
    private fun key(): String? = secrets.get("pc.key")?.toString(Charsets.UTF_8)

    fun isPaired(): Boolean = key() != null && prefs.getString("url",null)!=null && prefs.getString("fp",null)!=null

    /** Il segreto con cui telefono e PC si riconoscono via Bluetooth (nearby/NearbyCode.kt). */
    fun nearbySecret(): ByteArray? = key()?.let { org.aios.nova.nearby.NearbyCode.secretFromKey(it) }

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
        request(prefs.getString("url", null)!!, prefs.getString("fp", null)!!, key(), "POST", path, body)

    fun get(path: String): JSONObject = request(prefs.getString("url", null)!!, prefs.getString("fp", null)!!, key(), "GET", path, null)

    fun model(messages: JSONArray, tools: JSONArray): JSONObject =
        post("/api/modello", JSONObject().put("messages", messages).put("tools", tools)).getJSONObject("message")

    fun signed(method: String, path: String, body: JSONObject?): JSONObject {
        val raw = body?.toString()?.toByteArray(Charsets.UTF_8) ?: byteArrayOf()
        val auth = PhoneIdentity(context).sign(method, path, raw)
        return request(prefs.getString("url", null)!!, prefs.getString("fp", null)!!, null, method, path, body, auth)
    }

    fun name(): String = prefs.getString("pc", "PC")!!
    fun unpair() { secrets.remove("pc.key"); prefs.edit().clear().commit(); link(null, null) }

    /** Dal QR del PC: https://ip:porta/#abbina=CODICE&fp=IMPRONTA */
    fun pair(qr: String): String {
        val uri = Uri.parse(qr)
        require(uri.scheme == "https" && !uri.host.isNullOrBlank() && uri.userInfo == null) { "L'indirizzo del PC deve essere HTTPS" }
        val fragment = (uri.fragment ?: "").split("&").mapNotNull {
            val parts = it.split("=", limit = 2)
            if (parts.size == 2) parts[0] to parts[1] else null
        }.toMap()
        val code = fragment["abbina"] ?: throw IllegalArgumentException("codice QR non valido")
        val fp = fragment["fp"] ?: throw IllegalArgumentException("nel codice QR manca l'impronta del PC")
        require(fp.matches(Regex("[0-9a-fA-F]{64}"))) { "Impronta del PC non valida" }
        val port = if (uri.port == -1) 443 else uri.port
        require(port in 1..65535)
        val host = uri.host!!.let { if (it.contains(':') && !it.startsWith('[')) "[$it]" else it }
        val base = "https://$host:$port"
        val body = JSONObject().put("code", code).put("name", Build.MODEL).put("kind", "telefono")
        val identity = PhoneIdentity(context)
        body.put("device_key", identity.publicKey())
        bluetoothAddress()?.let { body.put("bt", it) }  // per collegarsi anche senza Wi-Fi
        val reply = request(base, fp, null, "POST", "/api/abbina", body)
        val receivedKey = reply.getString("key")
        require(receivedKey.matches(Regex("[A-Za-z0-9_-]{32,128}"))) { "Chiave di abbinamento non valida" }
        reply.optJSONObject("identita")?.let { identity.join(it) }
        secrets.set("pc.key", receivedKey.toByteArray())
        check(prefs.edit().putString("url", base).putString("fp", fp)
            .putString("pc", reply.optString("pc", "PC")).commit()) { "Impossibile conservare l'abbinamento" }
        return reply.optString("pc", "PC")
    }

    /** «Chiedi al PC»: il copilota del PC risponde, con strumenti limitati e conferme sul telefono. */
    fun ask(text: String, timeoutMs: Long = 300_000,
            confirm: (PcConfirmation, Long) -> Boolean = { _, _ -> false },
            onFile: (String, String) -> Unit = { _, _ -> }): String {
        require(text.trim().length in 1..500) { "La richiesta al PC deve avere al massimo 500 caratteri" }
        val url = prefs.getString("url", null)!!
        val fp = prefs.getString("fp", null)!!
        val key = key()!!
        val job = try {
            request(url, fp, key, "POST", "/api/chiedi", JSONObject().put("text", text)).getString("job")
        } catch (e: java.net.ConnectException) {
            throw e  // connessione mai stabilita: il modello locale può rispondere
        } catch (e: java.net.UnknownHostException) {
            throw e
        } catch (e: java.net.NoRouteToHostException) {
            throw e
        } catch (e: Exception) {
            // Un errore di lettura può arrivare dopo che il PC ha già accettato la domanda.
            throw PcJobException("Non posso verificare se il computer ha ricevuto la richiesta. Controlla sul PC prima di ripeterla.", e)
        }
        return PcJob({ method, path, body -> request(url, fp, key, method, path, body) },onFile=onFile)
            .await(job, timeoutMs, confirm)
    }

    private fun bluetoothAddress(): String? = try {
        context.getSystemService(android.bluetooth.BluetoothManager::class.java)?.adapter?.address
    } catch (e: SecurityException) {
        null
    }

    private fun request(base: String, fingerprint: String, key: String?, method: String, path: String,
                        body: JSONObject?, authorization: String? = null): JSONObject {
        val conn = connection(base, fingerprint, method, path, key, authorization)
        try {
            if (body != null) {
                conn.doOutput = true
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val status = conn.responseCode
            val stream = if (status in 200..299) conn.inputStream else conn.errorStream
            val data = stream?.use { it.readNBytes(8 * 1024 * 1024 + 1) } ?: byteArrayOf()
            require(data.size <= 8 * 1024 * 1024) { "Risposta del PC troppo grande" }
            val json = JSONObject(String(data, Charsets.UTF_8).ifEmpty { "{}" })
            if (status !in 200..299) throw IllegalStateException(json.optString("error", "errore $status"))
            return json
        } finally { conn.disconnect() }
    }

    private fun connection(base: String, fingerprint: String, method: String, path: String, key: String?, authorization: String? = null): HttpsURLConnection {
        require(path.startsWith("/") && !path.startsWith("//") && !path.contains('\r') && !path.contains('\n')) { "Percorso PC non valido" }
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
        conn.instanceFollowRedirects = false  // non inoltrare credenziali a una destinazione diversa
        conn.setRequestProperty("Content-Type", "application/json")
        if (authorization != null) conn.setRequestProperty("Authorization", authorization)
        else if (key != null) conn.setRequestProperty("Authorization", "Bearer $key")
        return conn
    }

    fun download(relative: String, dest: File) {
        val base = prefs.getString("url", null)!!
        val uri = Uri.parse(relative)
        val origin = Uri.parse(base)
        if (uri.isAbsolute) require(uri.scheme == "https" && uri.host == origin.host && uri.port == origin.port) { "Il file non proviene dal PC abbinato" }
        val path = (uri.encodedPath ?: error("Percorso file mancante")) + (uri.encodedQuery?.let { "?$it" } ?: "")
        require(path.startsWith("/scarica/")) { "Link file non valido" }
        val conn = connection(base, prefs.getString("fp", null)!!, "GET", path, key())
        try {
            require(conn.responseCode == 200) { "File non disponibile o link scaduto" }
            conn.inputStream.use { VerifiedFiles.install(it, dest, null, conn.contentLengthLong.takeIf { n -> n > 0 }, 2L * 1024 * 1024 * 1024) }
        } finally { conn.disconnect() }
    }

    fun upload(name: String, size: Long, input: InputStream): String {
        require(size in 1..(8L * 1024 * 1024 * 1024))
        val path = "/api/carica?nome=${Uri.encode(name)}"
        val conn = connection(prefs.getString("url", null)!!, prefs.getString("fp", null)!!, "POST", path, key())
        conn.doOutput = true; conn.setFixedLengthStreamingMode(size)
        conn.setRequestProperty("Content-Type", "application/octet-stream")
        try {
            conn.outputStream.use { output ->
                val block = ByteArray(128 * 1024); var sent = 0L
                while (true) {
                    if (Thread.currentThread().isInterrupted) throw InterruptedException("Invio annullato")
                    val n = input.read(block); if (n < 0) break
                    sent += n; require(sent <= size); output.write(block, 0, n)
                }
                require(sent == size) { "Foto o video incompleto" }
            }
            require(conn.responseCode in 200..299) { "Invio al PC non riuscito: HTTP ${conn.responseCode}" }
            return conn.inputStream.bufferedReader().use { JSONObject(it.readText()).optString("salvato", name) }
        } finally { conn.disconnect() }
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
