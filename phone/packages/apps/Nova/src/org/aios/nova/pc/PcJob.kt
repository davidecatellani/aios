package org.aios.nova.pc

import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Un'azione del PC richiede una decisione esplicita del proprietario del telefono. */
data class PcConfirmation(val label: String, val warning: String?)

/** Il job è già partito: non ripetere la domanda sul modello locale dopo un errore. */
class PcJobException(message: String, cause: Exception? = null) : Exception(message, cause)

/** Protocollo dei job, indipendente dalle API Android e verificabile con un PC simulato. */
class PcJob(
    private val request: (String, String, JSONObject?) -> JSONObject,
    private val now: () -> Long = { TimeUnit.NANOSECONDS.toMillis(System.nanoTime()) },
    private val pause: (Long) -> Unit = { Thread.sleep(it) },
    private val onFile: (String, String) -> Unit = { _, _ -> }
) {
    fun await(job: String, timeoutMs: Long, confirm: (PcConfirmation, Long) -> Boolean): String {
        require(timeoutMs > 0)
        val deadline = now() + timeoutMs
        var after = 0
        var lastConfirmation: Int? = null
        var waitingForClear = false
        val files=mutableSetOf<String>()
        try {
            while (now() < deadline) {
                val state = request("GET", "/api/job/$job?after=$after", null)
                state.optJSONArray("events")?.let { events ->
                    for(i in 0 until minOf(events.length(),1000)) {
                        val event=events.optJSONObject(i) ?: continue
                        if(event.optString("kind")!="file") continue
                        val url=event.optString("url")
                        if(url.matches(Regex("/scarica/[A-Za-z0-9_-]{8,256}")) && files.size<10 && files.add(url))
                            onFile(event.optString("name","Documento").take(180),url)
                    }
                }
                after = state.optInt("next", after)
                if (state.optBoolean("done")) return state.optString("answer")
                val pending = state.optJSONObject("pending")
                if (pending == null) {
                    waitingForClear = false
                } else {
                    val id = if (pending.has("id")) pending.getInt("id") else null
                    // I PC precedenti non hanno l'id: attendi che la conferma precedente sparisca.
                    val fresh = if (id != null) id != lastConfirmation else !waitingForClear
                    if (fresh) {
                        val remaining = deadline - now()
                        if (remaining <= 0) break
                        val decision = confirm(PcConfirmation(
                            pending.optString("label", "Confermi questa azione?"),
                            pending.optString("warning").takeIf { it.isNotBlank() }
                        ), remaining)
                        val body = JSONObject().put("ok", decision && now() < deadline)
                        if (id != null) body.put("id", id)
                        request("POST", "/api/job/$job/conferma", body)
                        lastConfirmation = id
                        waitingForClear = true
                    }
                }
                val remaining = deadline - now()
                if (remaining > 0) pause(minOf(700, remaining))
            }
            throw PcJobException("Il computer non ha risposto in tempo. La richiesta potrebbe essere ancora in corso: controlla sul PC prima di ripeterla.")
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            throw PcJobException("Hai chiuso la conversazione. Controlla sul PC lo stato della richiesta prima di ripeterla.", e)
        } catch (e: PcJobException) {
            throw e
        } catch (e: Exception) {
            throw PcJobException("Il collegamento con il computer si è interrotto. Controlla sul PC lo stato della richiesta prima di ripeterla.", e)
        }
    }
}
