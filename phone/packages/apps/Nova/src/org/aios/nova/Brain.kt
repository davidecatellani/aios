package org.aios.nova

import android.content.Context
import org.aios.nova.llm.LocalModel
import org.aios.nova.pc.PcBridge
import org.aios.nova.pc.PcConfirmation
import org.aios.nova.pc.PcJobException
import org.aios.nova.core.PhoneTools
import org.aios.nova.core.PrototypeStore
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Ragionamento locale o prestato dal PC; gli strumenti del telefono restano sul telefono. */
class Brain(private val context:Context) {
    private val pc=PcBridge(context)
    private val local=LocalModel(context)
    private val tools=PhoneTools(context)
    private val history=File(context.filesDir,"conversation.json")
    fun clear() { history.delete(); LocalModel.stop() }
    @Synchronized fun ask(text:String,confirm:(PcConfirmation,Long)->Boolean={_,_->false},onFile:(String,String)->Unit={_,_->}):String {
        require(text.length in 1..8000)
        tools.direct(text)?.let { return tools.execute(it,confirm) }
        if(text.startsWith("chiedi al pc ",true)) {
            require(pc.isPaired()) { "Abbina prima il PC" }
            return try { pc.ask(text.substring(13),confirm=confirm,onFile=onFile) }
            catch(e:PcJobException) { e.message ?: "Controlla sul PC lo stato della richiesta." }
        }
        val messages=JSONArray().put(JSONObject().put("role","system").put("content",
            "Sei Nova, assistente di SoIA su un telefono Android. Rispondi in italiano. " +
            "Per agire sul telefono usa esclusivamente lo strumento telefono; ogni modifica chiede conferma. " +
            "Non inventare risultati, app installate o dati personali. L'utente può chiedere esplicitamente al PC con 'chiedi al pc'. " +
            "Data locale: ${java.time.LocalDateTime.now()}. Profilo: ${PrototypeStore(context).records("profilo/")}"))
        if(history.isFile && history.length()<=128*1024) runCatching {
            val old=JSONArray(history.readText())
            for(i in maxOf(0,old.length()-12) until old.length()) messages.put(old.getJSONObject(i))
        }
        messages.put(JSONObject().put("role","user").put("content",text))
        val used=mutableSetOf<String>()
        var answer=""
        for(turn in 0 until 5) {
            val message=if(pc.isPaired()) {
                try { pc.model(messages,tools.schema()) } catch(e:Exception) {
                    if(e is InterruptedException) throw e
                    local.chat(messages,tools.schema())
                }
            } else local.chat(messages,tools.schema())
            messages.put(message)
            val calls=message.optJSONArray("tool_calls")
            if(calls==null || calls.length()==0) { answer=message.optString("content").trim(); break }
            require(calls.length()<=4) { "Troppe operazioni in una risposta" }
            for(i in 0 until calls.length()) {
                val call=calls.getJSONObject(i); val function=call.getJSONObject("function")
                val result=try {
                    require(function.getString("name")=="telefono") { "Strumento non consentito" }
                    val raw=function.get("arguments")
                    val args=if(raw is JSONObject) raw else JSONObject(raw.toString())
                    val signature=org.aios.nova.core.Crypto.canonical(args).decodeToString()
                    require(used.add(signature)) { "Operazione già eseguita in questa richiesta" }
                    tools.execute(args,confirm)
                } catch(e:Exception) { "Operazione non eseguita: ${e.message}" }
                messages.put(JSONObject().put("role","tool").put("tool_call_id",call.getString("id")).put("content",result))
            }
        }
        if(answer.isBlank()) answer="Ho raggiunto il limite di operazioni della richiesta. Controlla i risultati sul telefono."
        // Conserva solo domande e risposte concluse, senza sequenze di tool incomplete.
        val saved=JSONArray()
        for(i in 1 until messages.length()) {
            val message=messages.getJSONObject(i)
            if(message.optString("role")=="user" || (message.optString("role")=="assistant" && !message.has("tool_calls"))) saved.put(message)
        }
        while(saved.length()>12) saved.remove(0)
        val temp=File(context.filesDir,".conversation.tmp"); temp.writeText(saved.toString()); check(temp.renameTo(history))
        return answer
    }
}
