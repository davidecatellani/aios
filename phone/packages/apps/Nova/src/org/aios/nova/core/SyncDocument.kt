package org.aios.nova.core

import org.json.JSONObject
import org.json.JSONArray
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.Base64

/** LWW/HLC e ChaCha20-Poly1305, stesso protocollo del PC. Le cancellazioni conservano il tombstone. */
class SyncDocument(private val file: File, private var key: ByteArray, val device: String,
                   private val wall: () -> Long = { System.currentTimeMillis() }) {
    private var state: JSONObject
    private var ms = 0L
    private var counter = 0
    init {
        require(key.size==32 && device.matches(Regex("[0-9a-f]{16}")))
        require(!file.exists() || file.length()<=16*1024*1024) { "Archivio di sincronizzazione troppo grande" }
        state=if(file.exists()) JSONObject(file.readText()) else JSONObject().put("ops",JSONObject()).put("peers",JSONObject()).put("seq",0)
        val ops=state.getJSONObject("ops")
        for(h in ops.keys()) {
            val op=ops.getJSONObject(h)
            if(op.getLong("ms")>ms) { ms=op.getLong("ms"); counter=op.getInt("c") }
            else if(op.getLong("ms")==ms) counter=maxOf(counter,op.getInt("c"))
        }
    }
    private fun persist() {
        file.parentFile!!.mkdirs()
        val temp=File.createTempFile(".sync-", ".tmp",file.parentFile)
        try {
            val data=state.toString()
            require(data.toByteArray().size<=16*1024*1024) { "Archivio pieno: esporta i dati prima di aggiungere altre voci" }
            temp.writeText(data)
            Files.move(temp.toPath(),file.toPath(),StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE)
        } finally { temp.delete() }
    }
    private fun <T> transaction(block:()->T):T {
        val previous=JSONObject(state.toString()); val previousKey=key.copyOf(); val previousMs=ms; val previousCounter=counter
        try { val result=block(); persist(); return result }
        catch(e:Exception) { state=previous; key=previousKey; ms=previousMs; counter=previousCounter; throw e }
    }
    private fun hash(name:String)=Crypto.hex(Crypto.hmac(key,name.toByteArray())).take(32)
    private fun aad(op:JSONObject)="${op.getString("h")}|${op.getLong("ms")}|${op.getInt("c")}|${op.getString("d")}".toByteArray()
    private fun plaintext(op:JSONObject):JSONObject {
        val raw=Base64.getDecoder().decode(op.getString("box"))
        require(raw.size<=1024*1024)
        val doc=JSONObject(String(Crypto.open(key,raw,aad(op)),Charsets.UTF_8))
        require(hash(doc.getString("k"))==op.getString("h")) { "Chiave della voce alterata" }
        return doc
    }
    private fun put(op:JSONObject) {
        val seq=state.getLong("seq")+1
        state.getJSONObject("ops").put(op.getString("h"),JSONObject(op.toString()).put("seq",seq))
        state.put("seq",seq)
    }
    @Synchronized fun set(name:String,value:Any?) = transaction {
        require(name.length in 1..512)
        val now=wall()
        if(now>ms) { ms=now; counter=0 } else counter++
        val op=JSONObject().put("h",hash(name)).put("ms",ms).put("c",counter).put("d",device)
        val doc=JSONObject().put("k",name).put("v",value ?: JSONObject.NULL)
        require(Crypto.canonical(doc).size<=512*1024) { "Voce troppo grande" }
        op.put("box",Base64.getEncoder().encodeToString(Crypto.seal(key,Crypto.canonical(doc),aad(op))))
        put(op)
    }
    @Synchronized fun records(prefix:String=""):Map<String,Any> {
        val result=linkedMapOf<String,Any>()
        val ops=state.getJSONObject("ops")
        for(h in ops.keys()) {
            val doc=plaintext(ops.getJSONObject(h)); val name=doc.getString("k")
            if(name.startsWith(prefix) && !doc.isNull("v")) result[name]=doc.get("v")
        }
        return result
    }
    private fun newer(a:JSONObject,b:JSONObject):Boolean = when {
        a.getLong("ms")!=b.getLong("ms") -> a.getLong("ms")>b.getLong("ms")
        a.getInt("c")!=b.getInt("c") -> a.getInt("c")>b.getInt("c")
        else -> a.getString("d")>b.getString("d")
    }
    @Synchronized fun merge(incoming:JSONArray):Int = transaction {
        require(incoming.length()<=2000) { "Troppi elementi in una pagina" }
        var applied=0
        for(i in 0 until incoming.length()) {
            try {
                val op=incoming.getJSONObject(i)
                require(op.getString("h").matches(Regex("[0-9a-f]{32}")))
                require(op.getLong("ms")>=0 && op.getInt("c")>=0 && op.getString("d").length in 1..64)
                plaintext(op)
                val current=state.getJSONObject("ops").optJSONObject(op.getString("h"))
                if(current!=null && !newer(op,current)) continue
                if(op.getLong("ms")>ms) { ms=op.getLong("ms"); counter=op.getInt("c") }
                else if(op.getLong("ms")==ms) counter=maxOf(counter,op.getInt("c"))
                put(op); applied++
            } catch (_:IllegalArgumentException) { continue }
              catch (_:org.json.JSONException) { continue }
        }
        applied
    }
    @Synchronized fun page(after:Long,onlyLocal:Boolean=false):Pair<JSONArray,Long> {
        val ops=state.getJSONObject("ops")
        val candidates=ops.keys().asSequence().map { ops.getJSONObject(it) }
            .filter { it.getLong("seq")>after }.sortedBy { it.getLong("seq") }.take(2000).toList()
        val result=JSONArray()
        for(op in candidates) if(!onlyLocal || op.getString("d")==device) {
            val clean=JSONObject(op.toString()); clean.remove("seq"); result.put(clean)
        }
        return result to (candidates.lastOrNull()?.getLong("seq") ?: after)
    }
    @Synchronized fun peer(name:String):Pair<Long,Long> {
        val entry=state.getJSONObject("peers").optJSONObject(name) ?: JSONObject()
        return entry.optLong("received") to entry.optLong("sent")
    }
    @Synchronized fun peer(name:String,received:Long,sent:Long) = transaction {
        require(received>=0 && sent>=0)
        state.getJSONObject("peers").put(name,JSONObject().put("received",received).put("sent",sent))
    }
    @Synchronized fun rekey(newKey:ByteArray) {
        require(newKey.size==32)
        if(key.contentEquals(newKey)) return
        transaction {
        val ops=state.getJSONObject("ops")
        val entries=ops.keys().asSequence().map { val op=ops.getJSONObject(it); op to plaintext(op) }.toList()
        key=newKey; state=JSONObject().put("ops",JSONObject()).put("peers",JSONObject()).put("seq",0)
        for((old,doc) in entries) {
            val op=JSONObject(old.toString()).put("h",hash(doc.getString("k")))
            op.put("box",Base64.getEncoder().encodeToString(Crypto.seal(key,Crypto.canonical(doc),aad(op))))
            put(op)
        }
        }
    }
}
