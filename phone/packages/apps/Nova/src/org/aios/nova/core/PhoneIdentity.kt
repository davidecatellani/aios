package org.aios.nova.core

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.util.Base64

/** Certificato rilasciato dal PC; richieste firmate, revoche e rotazione delle chiavi. */
class PhoneIdentity(private val context:Context) {
    private val secrets=Secrets(context)
    private val file=File(context.filesDir,"identity.json")
    private fun seed():ByteArray = synchronized(LOCK) {
        secrets.get("identity.seed") ?: Crypto.bytes(32).also { secrets.set("identity.seed",it) }
    }
    fun publicKey():String=encode(Crypto.publicKey(seed()))
    fun deviceId():String=Crypto.hex(Crypto.hash(Crypto.publicKey(seed()))).take(16)
    fun data():JSONObject?=if(file.isFile) JSONObject(file.readText()) else null
    fun isReady():Boolean=data()!=null && secrets.get("identity.sync")!=null
    fun syncKey():ByteArray=secrets.get("identity.sync") ?: secrets.get("identity.offline") ?:
        Crypto.bytes(32).also { secrets.set("identity.offline",it) }
    fun previousKey():ByteArray?=secrets.get("identity.previous")
    fun clearPreviousKey() { secrets.remove("identity.previous") }
    private fun installKey(key:ByteArray) {
        val old=syncKey()
        if(!old.contentEquals(key)) secrets.set("identity.previous",old)
        secrets.set("identity.sync",key)
    }
    private fun persist(doc:JSONObject) {
        val temp=File(context.filesDir,".identity.tmp")
        temp.writeText(doc.toString()); check(temp.renameTo(file)) { "Impossibile conservare l'identità" }
    }
    private fun certificateBody(cert:JSONObject)=JSONObject().put("v",1).put("id",cert.getString("id"))
        .put("name",cert.getString("name")).put("kind",cert.getString("kind")).put("public",cert.getString("public")).put("issued",cert.getLong("issued"))
    @Synchronized fun join(bundle:JSONObject) {
        val cert=bundle.getJSONObject("certificato"); val user=decode(bundle.getString("utente"))
        require(cert.getString("id")==deviceId() && cert.getString("public")==publicKey()) { "Certificato di un altro telefono" }
        require(Crypto.verify(user,Crypto.canonical(certificateBody(cert)),decode(cert.getString("signature")))) { "Certificato dell'identità non valido" }
        bundle.optJSONObject("revoche")?.let { rev ->
            val body=JSONObject().put("v",rev.getInt("v")).put("seq",rev.getLong("seq")).put("revoked",rev.getJSONArray("revoked"))
            require(Crypto.verify(user,Crypto.canonical(body),decode(rev.getString("signature")))) { "Elenco revoche non valido" }
            require((0 until rev.getJSONArray("revoked").length()).none { rev.getJSONArray("revoked").getString(it)==deviceId() }) { "Il telefono è revocato" }
        }
        val sync=bundle.optString("sincronizzazione")
        if(sync.isNotEmpty()) { val bytes=decode(sync); require(bytes.size==32); installKey(bytes) }
        val doc=JSONObject(bundle.toString()); doc.remove("sincronizzazione"); persist(doc)
    }
    fun sign(method:String,path:String,body:ByteArray):String {
        val doc=data() ?: error("Completa l'identità SoIA sul PC prima di sincronizzare")
        val now=(System.currentTimeMillis()/1000).toString()
        val message="$method\n$path\n$now\n${Crypto.hex(Crypto.hash(body))}".toByteArray()
        val cert=Base64.getUrlEncoder().encodeToString(Crypto.canonical(doc.getJSONObject("certificato")))
        return "AIOS $cert.$now.${encode(Crypto.sign(seed(),message))}"
    }
    @Synchronized fun accept(reply:JSONObject):Boolean {
        val doc=data() ?: return false
        val user=decode(doc.getString("utente")); var changed=false
        reply.optJSONObject("revoche")?.let { rev ->
            val current=doc.optJSONObject("revoche")?.optLong("seq") ?: -1
            if(rev.getLong("seq")>current) {
                val body=JSONObject().put("v",rev.getInt("v")).put("seq",rev.getLong("seq")).put("revoked",rev.getJSONArray("revoked"))
                require(Crypto.verify(user,Crypto.canonical(body),decode(rev.getString("signature")))) { "Revoche non firmate dall'utente" }
                require((0 until rev.getJSONArray("revoked").length()).none { rev.getJSONArray("revoked").getString(it)==deviceId() }) { "Questo telefono è stato revocato dal PC" }
                doc.put("revoche",rev)
            }
        }
        reply.optJSONObject("chiavi")?.let { keys ->
            if(keys.has("epoch") && keys.getLong("epoch")>(doc.optJSONObject("chiavi")?.optLong("epoch") ?: 0)) {
                val body=JSONObject().put("v",keys.getInt("v")).put("epoch",keys.getLong("epoch")).put("wrapped",keys.getJSONObject("wrapped"))
                require(Crypto.verify(user,Crypto.canonical(body),decode(keys.getString("signature")))) { "Rotazione chiavi non firmata" }
                val item=keys.getJSONObject("wrapped").getJSONObject(deviceId())
                val info="${deviceId()}|${keys.getLong("epoch")}".toByteArray()
                val key=Crypto.unwrap(seed(),decode(item.getString("eph")),decode(item.getString("box")),info)
                require(key.size==32); installKey(key); doc.put("chiavi",keys); changed=true
            }
        }
        persist(doc); return changed
    }
    companion object {
        private val LOCK=Any()
        fun encode(bytes:ByteArray):String=Base64.getEncoder().encodeToString(bytes)
        fun decode(text:String):ByteArray=Base64.getDecoder().decode(text)
    }
}
