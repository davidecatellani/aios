package org.aios.nova.llm

import android.content.Context
import android.os.Handler
import android.os.Looper
import org.aios.nova.core.Crypto
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/** Modello incluso nella GSI: server locale autenticato, avviato su richiesta e chiuso a riposo. */
class LocalModel(private val context:Context) {
    private val models=ModelStore(context)
    fun isInstalled():Boolean = models.file("nova.gguf")!=null && File(SERVER).canExecute()
    fun downloadIfMissing() = models.downloadIfMissing()
    fun complete(prompt:String):String = chat(JSONArray().put(JSONObject().put("role","user").put("content",prompt)))
        .optString("content").trim()
    fun chat(messages:JSONArray,tools:JSONArray=JSONArray()):JSONObject = synchronized(LOCK) {
        ensureRunning()
        val body=JSONObject().put("messages",messages).put("max_tokens",384).put("temperature",0.3).put("stream",false)
        if(tools.length()>0) body.put("tools",tools).put("tool_choice","auto")
        try {
            request("/v1/chat/completions",body,180_000).getJSONArray("choices").getJSONObject(0).getJSONObject("message")
        } finally { scheduleStop() }
    }
    private fun request(path:String,body:JSONObject?,timeout:Int):JSONObject {
        val conn=URL("http://127.0.0.1:$PORT$path").openConnection() as HttpURLConnection
        conn.connectTimeout=2000; conn.readTimeout=timeout; conn.instanceFollowRedirects=false
        conn.setRequestProperty("Authorization","Bearer $token")
        try {
            if(body!=null) {
                conn.requestMethod="POST"; conn.doOutput=true; conn.setRequestProperty("Content-Type","application/json")
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val status=conn.responseCode
            require(status in 200..299) { "Il modello locale ha risposto con HTTP $status" }
            return conn.inputStream.bufferedReader().use { JSONObject(it.readText()) }
        } finally { conn.disconnect() }
    }
    private fun ensureRunning() {
        handler.removeCallbacks(stopTask)
        lastUse=android.os.SystemClock.elapsedRealtime()
        if(process?.isAlive==true) return
        val model=models.verified("nova.gguf")
        require(File(SERVER).canExecute()) { "Nell'immagine manca il motore del modello locale" }
        val log=File(context.cacheDir,"llama-server.log")
        token=Crypto.hex(Crypto.bytes(32))
        process=ProcessBuilder(SERVER,"-m",model.path,"--host","127.0.0.1","--port","$PORT",
            "--ctx-size","8192","--threads","4","--batch-size","128","--parallel","1",
            "--api-key",token,"--jinja","--log-disable","--no-warmup")
            .redirectErrorStream(true).redirectOutput(log).start()
        try {
            val deadline=android.os.SystemClock.elapsedRealtime()+90_000
            while(android.os.SystemClock.elapsedRealtime()<deadline) {
                if(Thread.currentThread().isInterrupted) throw InterruptedException("Richiesta annullata")
                check(process?.isAlive==true) { "Il motore locale si è chiuso durante l'avvio" }
                if(runCatching { request("/health",null,500); true }.getOrDefault(false)) return
                Thread.sleep(250)
            }
            error("Il modello locale non si è avviato entro 90 secondi")
        } catch(e:Exception) { stop(); throw e }
    }
    private fun scheduleStop() { lastUse=android.os.SystemClock.elapsedRealtime(); handler.removeCallbacks(stopTask); handler.postDelayed(stopTask,IDLE_MS) }
    companion object {
        const val PORT=11435
        const val IDLE_MS=60_000L
        private const val SERVER="/system_ext/bin/aios-llama-server"
        private val LOCK=Any()
        private var process:Process?=null
        private var token=""
        private val handler=Handler(Looper.getMainLooper())
        private var lastUse=0L
        // Il thread UI non deve aspettare il monitor mentre è in corso un'inferenza.
        private val stopTask:Runnable=Runnable { Thread {
            synchronized(LOCK) { if(android.os.SystemClock.elapsedRealtime()-lastUse>=IDLE_MS) stop() }
        }.start() }
        fun stop()=synchronized(LOCK) {
            handler.removeCallbacks(stopTask)
            process?.destroy(); process=null; token=""
        }
    }
}
