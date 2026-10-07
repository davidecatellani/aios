package org.aios.nova.core

import android.content.Context
import org.aios.nova.pc.PcBridge
import org.json.JSONObject
import java.io.File

/** Agenda, profilo e note funzionano offline e confluiscono nella sincronizzazione cifrata. */
class PrototypeStore(private val context:Context) {
    val identity=PhoneIdentity(context)
    val document:SyncDocument get()=synchronized(LOCK) {
        val key=identity.syncKey()
        if(shared==null || owner!=context.filesDir.path) {
            shared=SyncDocument(File(context.filesDir,"sync.json"),identity.previousKey() ?: key,identity.deviceId()); owner=context.filesDir.path
        }
        shared!!.rekey(key)
        identity.clearPreviousKey()
        shared!!
    }
    fun set(key:String,value:Any?)=document.set(key,value)
    fun records(prefix:String="")=document.records(prefix)
    fun sync():String=synchronized(LOCK) {
        val pc=PcBridge(context)
        require(pc.isPaired()) { "Abbina prima il PC" }
        require(identity.isReady()) { "Il PC deve avere un'identità SoIA configurata: poi ripeti l'abbinamento" }
        val doc=document
        var (received,sent)=doc.peer("pc")
        var applied=0; var pages=0
        while(pages<25) {
            val reply=pc.signed("GET","/api/sync?dopo=$received",null)
            if(identity.accept(reply)) { doc.rekey(identity.syncKey()); received=0; sent=0; pages++; continue }
            val ops=reply.getJSONArray("ops"); applied+=doc.merge(ops)
            val next=reply.getLong("seq"); require(next>=received) { "Contatore di sincronizzazione non valido" }
            received=next; doc.peer("pc",received,sent); pages++
            if(ops.length()<2000) break
        }
        var uploaded=0
        for(i in 0 until 25) {
            val (ops,last)=doc.page(sent,true)
            if(last==sent) break
            if(ops.length()>0) { pc.signed("POST","/api/sync",JSONObject().put("ops",ops)); uploaded+=ops.length() }
            sent=last; doc.peer("pc",received,sent)
        }
        "Sincronizzato: $applied voci ricevute, $uploaded inviate."
    }
    fun exportJson():String {
        val items=JSONObject(); for((key,value) in records()) {
            if(key.startsWith("agenda/") || key.startsWith("profilo/") || key.startsWith("note/") || key.startsWith("preferenze/")) items.put(key,value)
        }
        return JSONObject().put("format","soia-prototype-backup-v1").put("records",items).toString(2)
    }
    fun importJson(text:String):Int {
        require(text.toByteArray().size<=4*1024*1024) { "Backup troppo grande" }
        val doc=JSONObject(text); require(doc.getString("format")=="soia-prototype-backup-v1")
        val entries=doc.getJSONObject("records"); require(entries.length()<=2000)
        val keys=entries.keys().asSequence().toList()
        require(keys.all { it.startsWith("agenda/") || it.startsWith("profilo/") || it.startsWith("note/") || it.startsWith("preferenze/") })
        require(keys.all { key -> key.length in 1..512 && Crypto.canonical(JSONObject().put("k",key).put("v",entries.get(key))).size<=512*1024 })
        for(key in keys) set(key,entries.get(key))
        return keys.size
    }
    companion object { private val LOCK=Any(); private var shared:SyncDocument?=null; private var owner="" }
}
