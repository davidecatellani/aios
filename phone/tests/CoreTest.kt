package org.aios.nova.tests

import org.aios.nova.core.Crypto
import org.aios.nova.core.VerifiedFiles
import org.aios.nova.core.SyncDocument
import org.json.JSONObject
import org.json.JSONArray
import java.io.File
import java.util.Base64

fun main(args:Array<String>) {
    val fixture=JSONObject(File(args[0]).readText()); val root=File(args[1]); var count=0
    fun test(name:String,block:()->Unit) { block(); println("PASS $name"); count++ }
    fun raw(name:String)=Base64.getDecoder().decode(fixture.getString(name))
    fun rejected(block:()->Unit) { check(runCatching(block).isFailure) }
    val seed=raw("seed"); val key=raw("key"); val plain=raw("plain"); val aad=raw("aad")
    test("Ed25519: firma e chiave pubblica interoperabili con il PC") {
        check(Crypto.publicKey(seed).contentEquals(raw("public")))
        check(Crypto.sign(seed,plain).contentEquals(raw("signature")))
        check(Crypto.signReference(seed,plain).contentEquals(raw("signature")))
        check(Crypto.verify(raw("public"),plain,raw("signature")))
        check(!Crypto.verify(raw("public"),plain+byteArrayOf(1),raw("signature")))
        check(!Crypto.verify(ByteArray(32),plain,raw("signature")))
    }
    test("ChaCha20-Poly1305: vettore prodotto dal PC e dati alterati") {
        val box=raw("box"); val nonce=box.copyOfRange(0,12)
        check(Crypto.seal(key,plain,aad,nonce).contentEquals(box))
        check(Crypto.open(key,box,aad).contentEquals(plain))
        rejected { Crypto.open(key,box,aad+byteArrayOf(1)) }
        box[15]=(box[15].toInt() xor 1).toByte(); rejected { Crypto.open(key,box,aad) }
    }
    test("Rotazione chiavi: X25519, HKDF e apertura del pacchetto PC") {
        check(Crypto.unwrap(seed,raw("eph"),raw("wrapped"),raw("info")).contentEquals(key))
    }
    test("JSON canonico italiano compatibile con le firme del PC") {
        check(Crypto.canonical(fixture.getJSONObject("canonical_doc")).contentEquals(raw("canonical")))
    }
    val file=File(root,"sync.json"); val doc=SyncDocument(file,key,"1111111111111111") { 100000 }
    test("Sincronizzazione PC → telefono; riapertura e autenticazione") {
        check(doc.merge(fixture.getJSONArray("ops"))==1)
        check(doc.records()["note/pc"]=="Caffè sul PC")
        check(SyncDocument(file,key,"1111111111111111").records()["note/pc"]=="Caffè sul PC")
        val bad=JSONObject(fixture.getJSONArray("ops").getJSONObject(0).toString()).put("ms",999999)
        check(doc.merge(JSONArray().put(bad))==0)
    }
    test("Conflitti concorrenti, cancellazioni e rotazione conservano i dati") {
        val peer=SyncDocument(File(root,"peer.json"),key,"ffffffffffffffff") { 100000 }
        doc.set("note/test","telefono"); peer.set("note/test","tablet")
        check(doc.merge(peer.page(0).first)==1); check(doc.records()["note/test"]=="tablet")
        doc.set("note/test",null); check(!doc.records().containsKey("note/test"))
        peer.merge(doc.page(0).first); check(!peer.records().containsKey("note/test"))
        val next=Crypto.hash(key); doc.rekey(next); check(doc.records()["note/pc"]=="Caffè sul PC"); doc.rekey(key)
        check(doc.peer("pc")== (0L to 0L))
    }
    test("Importazione atomica: errore o interruzione preserva il file precedente") {
        val target=File(root,"model.gguf"); target.writeText("precedente")
        val content="GGUFprototipo".toByteArray(); val hash=Crypto.hex(Crypto.hash(content))
        rejected { VerifiedFiles.install(content.inputStream(),target,"0".repeat(64),content.size.toLong(),100) }
        check(target.readText()=="precedente")
        rejected { VerifiedFiles.install(content.inputStream(),target,hash,3,100) }
        check(target.readText()=="precedente")
        Thread.currentThread().interrupt(); rejected { VerifiedFiles.install(content.inputStream(),target,hash,null,100) }; Thread.interrupted()
        check(target.readText()=="precedente")
        VerifiedFiles.install(content.inputStream(),target,hash,content.size.toLong(),100,"GGUF".toByteArray())
        check(target.readBytes().contentEquals(content)); check(root.listFiles()!!.none { it.name.startsWith(".download-") })
    }
    doc.set("note/android","Caffè sul telefono")
    File(root,"android-ops.json").writeText(doc.page(0,true).first.toString())
    println("$count test core superati")
}
