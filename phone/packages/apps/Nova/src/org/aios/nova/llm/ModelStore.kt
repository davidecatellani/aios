package org.aios.nova.llm

import android.content.Context
import android.net.Uri
import org.aios.nova.core.VerifiedFiles
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.zip.ZipInputStream

/** Modelli già nell'immagine; download di riparazione e importazione esplicita dal selettore Android. */
class ModelStore(private val context:Context) {
    private val system=File("/system_ext/etc/aios/models")
    private val privateDir=File(context.filesDir,"modelli")
    private val prefs=context.getSharedPreferences("models",Context.MODE_PRIVATE)
    private fun spec(name:String):JSONObject {
        val file=File(system,"manifest.json")
        require(file.isFile && file.length()<=1024*1024) { "Nell'immagine manca il manifest dei modelli" }
        val items=JSONObject(file.readText()).getJSONArray("artifacts")
        return (0 until items.length()).map { items.getJSONObject(it) }.first { it.getString("name")==name }
    }
    fun file(name:String):File? {
        require(name in listOf("nova.gguf","whisper.bin","voice-data.zip"))
        val user=File(privateDir,name)
        if(user.isFile && user.length()>0) return user
        return File(system,name).takeIf { it.isFile && it.length()>0 }
    }
    @Synchronized fun verified(name:String):File {
        val file=file(name) ?: throw IllegalStateException("Manca $name: apri Modelli e scaricalo o importalo")
        val expected=if(file.parentFile==privateDir && name=="nova.gguf" && prefs.contains("imported_sha"))
            prefs.getString("imported_sha",null)!! else spec(name).getString("sha256")
        val marker="$expected:${file.length()}:${file.lastModified()}"
        if(prefs.getString("verified_$name",null)!=marker) {
            require(VerifiedFiles.sha256(file)==expected) { "Modello $name alterato o incompleto" }
            val magic=when(name) { "nova.gguf" -> "GGUF"; "whisper.bin" -> "lmgg"; else -> null }
            if(magic!=null) file.inputStream().use { require(String(it.readNBytes(4),Charsets.US_ASCII)==magic) { "Formato modello non valido" } }
            prefs.edit().putString("verified_$name",marker).apply()
        }
        return file
    }
    @Synchronized fun downloadIfMissing(progress:(String,Long)->Unit={_,_->}) {
        for(name in listOf("nova.gguf","whisper.bin")) if(file(name)==null) download(name,progress)
    }
    fun download(name:String,progress:(String,Long)->Unit={_,_->}) {
        val item=spec(name); var url=URL(item.getString("url")); var conn:HttpURLConnection?=null
        try {
            for(attempt in 0..5) {
                require(url.protocol=="https") { "Sono accettati soltanto download HTTPS" }
                conn=url.openConnection() as HttpURLConnection
                conn.connectTimeout=15_000; conn.readTimeout=60_000; conn.instanceFollowRedirects=false
                val status=conn.responseCode
                if(status in 300..399) {
                    val location=conn.getHeaderField("Location") ?: error("Redirect senza destinazione")
                    val next=URL(url,location); conn.disconnect(); conn=null; url=next
                    if(attempt==5) error("Troppi redirect")
                    continue
                }
                require(status==200) { "Download non riuscito: HTTP $status" }
                val magic=when(name) { "nova.gguf" -> "GGUF".toByteArray(); "whisper.bin" -> "lmgg".toByteArray(); else -> null }
                conn.inputStream.use { input -> VerifiedFiles.install(input,File(privateDir,name),item.getString("sha256"),
                    item.getLong("size"),3L*1024*1024*1024,magic) { progress(name,it) } }
                if(name=="nova.gguf") prefs.edit().remove("imported_sha").apply()
                return
            }
        } finally { conn?.disconnect() }
    }
    @Synchronized fun importModel(uri:Uri):String {
        LocalModel.stop()
        val stream=context.contentResolver.openInputStream(uri) ?: error("Non posso aprire il modello scelto")
        val hash=stream.use { VerifiedFiles.install(it,File(privateDir,"nova.gguf"),null,null,3L*1024*1024*1024,"GGUF".toByteArray()) }
        prefs.edit().putString("imported_sha",hash).remove("verified_nova.gguf").apply()
        return hash
    }
    @Synchronized fun voiceData():File {
        val archive=verified("voice-data.zip")
        val root=File(context.filesDir,"voce"); val marker=File(root,".version")
        val hash=VerifiedFiles.sha256(archive)
        if(marker.isFile && marker.readText()==hash) return root
        root.mkdirs()
        var total=0L
        ZipInputStream(archive.inputStream()).use { zip ->
            while(true) {
                val entry=zip.nextEntry ?: break
                val dest=File(root,entry.name).canonicalFile
                require(dest.toPath().startsWith(root.canonicalFile.toPath()) && dest!=root.canonicalFile) { "Archivio voce non valido" }
                if(entry.isDirectory) dest.mkdirs() else {
                    dest.parentFile!!.mkdirs()
                    dest.outputStream().use { out ->
                        val bytes=ByteArray(16384)
                        while(true) { val n=zip.read(bytes); if(n<0) break; total+=n; require(total<=64L*1024*1024); out.write(bytes,0,n) }
                    }
                }
                zip.closeEntry()
            }
        }
        marker.writeText(hash); return root
    }
    fun status():String = listOf("nova.gguf","whisper.bin","voice-data.zip").joinToString("\n") {
        val file=file(it); "$it: ${if(file!=null) "presente (${file.length()/1024/1024} MiB)" else "mancante"}"
    }
}
