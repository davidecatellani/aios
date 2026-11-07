package org.aios.nova.pc

import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
import android.content.Intent
import android.provider.OpenableColumns
import android.widget.*
import android.view.MotionEvent
import org.json.JSONObject
import org.json.JSONArray
import java.io.File
import java.util.concurrent.Executors

/** File del PC via TLS con impronta fissata; destinazione scelta dal selettore Android. */
class PcFilesActivity:Activity() {
    private val worker=Executors.newSingleThreadExecutor()
    private val pc by lazy { PcBridge(applicationContext) }
    private lateinit var status:TextView
    private lateinit var files:LinearLayout
    private var path=""
    private var pendingDownload:String?=null
    private var pendingTicket:String?=null
    private var busy=false
    private fun task(block:()->String) {
        if(busy) return
        busy=true; status.text="Operazione in corso…"
        worker.execute {
            val result=try { block() } catch(e:Exception) { "Non riuscito: ${e.message}. Un invio già iniziato non viene ripetuto automaticamente." }
            runOnUiThread { if(!isDestroyed) { busy=false; status.text=result } }
        }
    }
    private fun inputEvent(event:JSONObject) { task { pc.post("/api/input",JSONObject().put("eventi",JSONArray().put(event))); "Comando inviato al PC." } }
    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState)
        val root=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL; setPadding(24,24,24,24) }
        setContentView(ScrollView(this).apply { addView(root) })
        status=TextView(this).apply { text="File e telecomando: ${if(pc.isPaired()) pc.name() else "abbina prima il PC"}" }; root.addView(status)
        fun button(label:String,action:()->Unit) { root.addView(Button(this).apply { text=label; setOnClickListener { action() } }) }
        button("Cartella principale del PC") { browse("") }
        button("Cartella precedente") { browse(path.substringBeforeLast('/',"")) }
        val search=EditText(this).apply { hint="Cerca file sul PC" }; root.addView(search)
        button("Cerca") { task { val reply=pc.get("/api/cerca?q=${android.net.Uri.encode(search.text.toString())}")
            showEntries(reply.getJSONArray("results")); "Risultati della ricerca." } }
        button("Invia foto o video al PC") {
            startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
                .putExtra(Intent.EXTRA_MIME_TYPES,arrayOf("image/*","video/*")),2)
        }
        files=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL }; root.addView(files)
        root.addView(TextView(this).apply { text="Touchpad PC: trascina e rilascia per muovere il puntatore"; textSize=17f; setPadding(24,80,24,80)
            var x=0f; var y=0f
            setOnTouchListener { _,event ->
                when(event.action) {
                    MotionEvent.ACTION_DOWN -> { x=event.x; y=event.y }
                    MotionEvent.ACTION_UP -> { val dx=(event.x-x).toInt().coerceIn(-400,400); val dy=(event.y-y).toInt().coerceIn(-400,400)
                        inputEvent(JSONObject().put("tipo","muovi").put("dx",dx).put("dy",dy)) }
                }; true
            } })
        button("Clic sinistro") { inputEvent(JSONObject().put("tipo","click").put("tasto","sinistro")) }
        button("Scorri in alto") { inputEvent(JSONObject().put("tipo","scorri").put("passi",3)) }
        button("Scorri in basso") { inputEvent(JSONObject().put("tipo","scorri").put("passi",-3)) }
        val typing=EditText(this).apply { hint="Testo da scrivere sul PC"; filters=arrayOf(android.text.InputFilter.LengthFilter(2000)) }; root.addView(typing)
        button("Scrivi sul PC") { val text=typing.text.toString(); require(text.length<=2000)
            AlertDialog.Builder(this).setMessage("Scrivere questo testo nella finestra attiva del PC?\n$text")
                .setPositiveButton("Scrivi") { _,_ -> inputEvent(JSONObject().put("tipo","testo").put("testo",text)) }.setNegativeButton("Annulla",null).show() }
        button("Invio") { inputEvent(JSONObject().put("tipo","tasto").put("tasto","invio")) }
        val ticket=intent.getStringExtra("pc_ticket")
        if(ticket!=null && ticket.matches(Regex("/scarica/[A-Za-z0-9_-]{8,256}"))) {
            pendingTicket=ticket
            startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/octet-stream")
                .putExtra(Intent.EXTRA_TITLE,intent.getStringExtra("pc_name") ?: "Documento"),1)
        } else if(pc.isPaired()) browse("")
    }
    private fun browse(next:String) { task { val reply=pc.get("/api/cartella?p=${android.net.Uri.encode(next)}")
        path=reply.getString("path"); showEntries(reply.getJSONArray("entries")); "Cartella: ${path.ifEmpty { "/" }}" } }
    private fun showEntries(entries:JSONArray) { runOnUiThread { if(isDestroyed) return@runOnUiThread; files.removeAllViews()
        for(i in 0 until minOf(entries.length(),300)) {
            val entry=entries.getJSONObject(i); val entryPath=entry.getString("path"); val name=entry.getString("name")
            files.addView(Button(this).apply { text="${if(entry.optBoolean("dir")) "📁" else "📄"} $name"
                setOnClickListener {
                    if(entry.optBoolean("dir")) browse(entryPath)
                    else { pendingDownload=entryPath; startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                        .setType("application/octet-stream").putExtra(Intent.EXTRA_TITLE,name),1) }
                } })
        }
    } }
    override fun onActivityResult(code:Int,result:Int,data:Intent?) {
        super.onActivityResult(code,result,data); if(result!=RESULT_OK) return
        val uri=data?.data ?: return
        if(code==1) {
            val source=pendingDownload; val ticket=pendingTicket; pendingDownload=null; pendingTicket=null
            if(source==null && ticket==null) return
            task { val url=ticket ?: pc.get("/api/link?p=${android.net.Uri.encode(source)}").getString("url")
                val temp=File(cacheDir,"pc-download")
                try { pc.download(url,temp); contentResolver.openOutputStream(uri,"wt")!!.use { output -> temp.inputStream().use { it.copyTo(output) } }; "File salvato." }
                finally { temp.delete() } }
        } else if(code==2) {
            var name="foto.jpg"; var size=-1L
            contentResolver.query(uri,arrayOf(OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE),null,null,null)?.use { cursor ->
                if(cursor.moveToFirst()) { name=cursor.getString(0); if(!cursor.isNull(1)) size=cursor.getLong(1) }
            }
            if(size<=0) { status.text="Il fornitore del file non indica la dimensione: scegli un file locale."; return }
            AlertDialog.Builder(this).setMessage("Inviare $name al PC?").setPositiveButton("Invia") { _,_ -> task {
                contentResolver.openInputStream(uri)!!.use { pc.upload(name,size,it) }; "File inviato al PC." } }.setNegativeButton("Annulla",null).show()
        }
    }
    override fun onDestroy() { worker.shutdownNow(); super.onDestroy() }
}
