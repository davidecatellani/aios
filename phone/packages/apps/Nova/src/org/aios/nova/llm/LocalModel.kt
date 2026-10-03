package org.aios.nova.llm

import android.content.Context
import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Il modello AI del telefono (llama.cpp). Si avvia solo quando serve e si spegne dopo un minuto
 * senza domande: in memoria non resta niente che consumi batteria.
 */
class LocalModel(private val context: Context) {
    private val modelFile = File(context.filesDir, "modelli/nova.gguf")
    private val server = File(context.applicationInfo.nativeLibraryDir, "libaios_llama_server.so")

    fun isInstalled(): Boolean = modelFile.exists() && server.exists()

    fun complete(prompt: String): String {
        ensureRunning()
        val conn = URL("http://127.0.0.1:$PORT/completion").openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        conn.outputStream.use { it.write(JSONObject().put("prompt", prompt).put("n_predict", 256).toString().toByteArray()) }
        val text = conn.inputStream.bufferedReader().use { it.readText() }
        scheduleStop()
        return JSONObject(text).optString("content").trim()
    }

    /** Solo dal lavoro «pesante» (in carica, Wi-Fi, telefono fermo). */
    fun downloadIfMissing() {
        // Il modello si sceglie come sul PC (models.py: catalogo firmato, impronta verificata):
        // qui verrà scaricato da quell'elenco, adatto alla RAM del telefono.
    }

    private fun ensureRunning() {
        synchronized(LOCK) {
            if (process?.isAlive == true) return
            process = ProcessBuilder(server.path, "-m", modelFile.path, "--host", "127.0.0.1", "--port", "$PORT",
                "-c", "2048", "-t", "4")
                .redirectErrorStream(true).start()
            val deadline = System.currentTimeMillis() + 30_000
            while (System.currentTimeMillis() < deadline) {
                val ready = try {
                    val probe = URL("http://127.0.0.1:$PORT/health").openConnection() as HttpURLConnection
                    probe.connectTimeout = 500
                    probe.responseCode == 200
                } catch (e: Exception) {
                    false
                }
                if (ready) return
                Thread.sleep(300)
            }
            throw IllegalStateException("il modello del telefono non si è avviato")
        }
    }

    private fun scheduleStop() {
        handler.removeCallbacksAndMessages(null)
        handler.postDelayed({ synchronized(LOCK) { process?.destroy(); process = null } }, IDLE_MS)
    }

    companion object {
        const val PORT = 11435
        const val IDLE_MS = 60_000L
        private val LOCK = Any()
        private var process: Process? = null
        private val handler = Handler(Looper.getMainLooper())
    }
}
