package org.aios.nova.tests

import org.aios.nova.pc.PcDecision
import org.aios.nova.pc.PcJob
import org.aios.nova.pc.PcJobException
import org.json.JSONObject

/** Test JVM del protocollo Android: nessun telefono, modello o rete necessari. */
fun main() {
    var count = 0
    fun test(name: String, block: () -> Unit) {
        block()
        count++
        println("PASS $name")
    }
    fun state(id: Int? = null, done: Boolean = false): JSONObject = JSONObject()
        .put("done", done).put("answer", "Fatto").put("next", 1)
        .apply {
            if (!done) put("pending", JSONObject().put("label", "Invia la mail a Giulia")
                .put("warning", "Contiene dati privati").apply { if (id != null) put("id", id) })
        }

    for (approved in listOf(true, false)) test("decisione esplicita: $approved") {
        var clock = 0L
        val posts = mutableListOf<JSONObject>()
        val job = PcJob({ method, path, body ->
            check(path.startsWith("/api/job/7"))
            if (method == "POST") {
                posts += body!!
                JSONObject().put("ok", true)
            } else state(1, done = posts.isNotEmpty())
        }, { clock }, { clock += it })
        check(job.await("7", 5000) { pending, remaining ->
            check(pending.label == "Invia la mail a Giulia")
            check(pending.warning == "Contiene dati privati")
            check(remaining == 5000L)
            approved
        } == "Fatto")
        check(posts.single().getBoolean("ok") == approved)
        check(posts.single().getInt("id") == 1)
    }

    test("due conferme consecutive, nessuna ripetizione della precedente") {
        var clock = 0L
        var polls = 0
        val shown = mutableListOf<Int>()
        val posted = mutableListOf<Int>()
        val job = PcJob({ method, _, body ->
            if (method == "POST") {
                posted += body!!.getInt("id")
                JSONObject().put("ok", true)
            } else when (polls++) {
                0, 1 -> state(1)
                2 -> state(2)
                else -> state(done = true)
            }
        }, { clock }, { clock += it })
        job.await("7", 5000) { _, _ -> shown += shown.size + 1; true }
        check(shown == listOf(1, 2) && posted == listOf(1, 2))
    }

    test("compatibilità con PC senza id di conferma") {
        var clock = 0L
        var polls = 0
        var prompts = 0
        val posts = mutableListOf<JSONObject>()
        val job = PcJob({ method, _, body ->
            if (method == "POST") { posts += body!!; JSONObject().put("ok", true) }
            else when (polls++) {
                0, 1 -> state()
                2 -> JSONObject().put("done", false)
                3 -> state()
                else -> state(done = true)
            }
        }, { clock }, { clock += it })
        job.await("7", 5000) { _, _ -> prompts++; false }
        check(prompts == 2 && posts.size == 2 && posts.none { it.has("id") })
    }

    test("una conferma oltre la scadenza viene rifiutata") {
        var clock = 0L
        var sent: Boolean? = null
        val job = PcJob({ method, _, body ->
            if (method == "POST") { sent = body!!.getBoolean("ok"); JSONObject().put("ok", true) }
            else state(1)
        }, { clock }, { clock += it })
        val failure = runCatching { job.await("7", 1000) { _, _ -> clock = 1001; true } }.exceptionOrNull()
        check(failure is PcJobException && sent == false)
    }

    test("perdita di rete dopo invio: errore distinto dal PC assente") {
        val job = PcJob({ _, _, _ -> throw java.io.IOException("offline") })
        check(runCatching { job.await("7", 1000) { _, _ -> true } }.exceptionOrNull() is PcJobException)
    }

    test("timeout di una richiesta senza conferma") {
        var clock = 0L
        val job = PcJob({ _, _, _ -> JSONObject().put("done", false) }, { clock }, { clock += it })
        check(runCatching { job.await("7", 1000) { _, _ -> error("nessuna conferma") } }
            .exceptionOrNull() is PcJobException)
    }

    test("chiusura del dialogo: annullamento definitivo") {
        val decision = PcDecision()
        decision.answer(false)
        decision.answer(true)
        check(!decision.await(1000))
    }
    test("nessuna risposta: annullamento") {
        check(!PcDecision().await(0))
    }
    test("interruzione: annullamento e flag preservato") {
        Thread.currentThread().interrupt()
        check(!PcDecision().await(1000))
        check(Thread.interrupted())
    }
    test("allegati PC: raccolti anche nel risultato finale, duplicati e link esterni ignorati") {
        val files=mutableListOf<String>()
        val events=org.json.JSONArray().put(JSONObject().put("kind","file").put("name","documento.pdf").put("url","/scarica/abcdefgh1234"))
            .put(JSONObject().put("kind","file").put("name","duplicato").put("url","/scarica/abcdefgh1234"))
            .put(JSONObject().put("kind","file").put("name","esterno").put("url","https://example.org/secret"))
        val job=PcJob({ _,_,_ -> state(done=true).put("events",events) },onFile={ name,url -> files+="$name:$url" })
        check(job.await("7",1000) { _,_-> false }=="Fatto")
        check(files==listOf("documento.pdf:/scarica/abcdefgh1234"))
    }
    println("$count test superati")
}
