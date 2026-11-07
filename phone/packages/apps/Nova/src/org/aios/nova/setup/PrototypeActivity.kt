package org.aios.nova.setup

import android.app.Activity
import android.app.AlertDialog
import android.app.role.RoleManager
import android.os.Bundle
import android.content.Intent
import android.content.pm.PackageManager
import android.Manifest
import android.provider.Settings
import android.widget.*
import org.aios.nova.assistant.WakeService
import org.aios.nova.core.PrototypeStore
import org.aios.nova.core.PhoneTools
import org.aios.nova.core.Reminders
import org.aios.nova.llm.ModelStore
import org.aios.nova.pc.PcBridge
import org.aios.nova.pc.PcFilesActivity
import org.aios.nova.Brain
import java.util.concurrent.Executors

/** Tutte le funzioni del prototipo raggiungibili da una sola schermata. */
class PrototypeActivity:Activity() {
    private val worker=Executors.newSingleThreadExecutor()
    private lateinit var status:TextView
    private lateinit var root:LinearLayout
    private val store by lazy { PrototypeStore(applicationContext) }
    private fun button(label:String,action:()->Unit) { root.addView(Button(this).apply { text=label; setOnClickListener { action() } }) }
    private fun task(action:()->String) { status.text="Operazione in corso…"; worker.execute {
        val result=try { action() } catch(e:Exception) { "Non riuscito: ${e.message}" }
        runOnUiThread { if(!isDestroyed && !isFinishing) status.text=result }
    } }
    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState)
        root=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL; setPadding(24,24,24,24) }
        setContentView(ScrollView(this).apply { addView(root) })
        status=TextView(this).apply { text="SoIA smartphone — prototipo integrato\n${ModelStore(this@PrototypeActivity).status()}"; textSize=16f }; root.addView(status)
        button("Permessi: voce, torcia, Bluetooth e promemoria") {
            val permissions=arrayOf(Manifest.permission.RECORD_AUDIO,Manifest.permission.CAMERA,Manifest.permission.BLUETOOTH_SCAN,
                Manifest.permission.BLUETOOTH_CONNECT,Manifest.permission.BLUETOOTH_ADVERTISE,Manifest.permission.POST_NOTIFICATIONS)
            val missing=permissions.filter { checkSelfPermission(it)!=PackageManager.PERMISSION_GRANTED }
            if(missing.isNotEmpty()) requestPermissions(missing.toTypedArray(),1)
        }
        button("Scegli Nova come assistente") {
            val roles=getSystemService(RoleManager::class.java)
            if(roles?.isRoleAvailable(RoleManager.ROLE_ASSISTANT)==true) startActivityForResult(roles.createRequestRoleIntent(RoleManager.ROLE_ASSISTANT),2)
            else startActivity(Intent(Settings.ACTION_VOICE_INPUT_SETTINGS))
        }
        val prefs=getSharedPreferences("prototype",MODE_PRIVATE)
        root.addView(CheckBox(this).apply { text="Leggi le risposte con voce italiana offline"; isChecked=prefs.getBoolean("speak",false)
            setOnCheckedChangeListener { _,checked -> prefs.edit().putBoolean("speak",checked).apply() } })
        button("Attiva Ehi Nova (software, usa microfono e batteria)") {
            if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED ||
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED) {
                status.text="Concedi prima i permessi microfono e notifiche."; return@button
            }
            AlertDialog.Builder(this).setTitle("Ascolto offline").setMessage("Il microfono resterà attivo con notifica visibile. La modalità software può consumare molta batteria; si ferma sotto il 15%. Non si riattiva dopo il riavvio.")
                .setPositiveButton("Attiva") { _,_ -> startForegroundService(Intent(this,WakeService::class.java)) }.setNegativeButton("Annulla",null).show()
        }
        button("Arresta Ehi Nova") { stopService(Intent(this,WakeService::class.java)) }
        button("Collega PC / ripristina identità dal PC") { startActivity(Intent(this,RestoreActivity::class.java)) }
        button("Sincronizza agenda, note e profilo") { task { store.sync().also { Reminders.schedule(this) } } }
        button("File, foto e telecomando del PC") { startActivity(Intent(this,PcFilesActivity::class.java)) }
        button("KDE Connect: notifiche, SMS, foto, appunti e PC") { launch("org.kde.kdeconnect_tp") }
        button("Play Store") { launch("com.android.vending") }
        button("Impostazioni Google") {
            val intent=Intent("com.google.android.gms.settings.GOOGLE_SETTINGS").setPackage("com.google.android.gms")
            if(intent.resolveActivity(packageManager)!=null) startActivity(intent) else startActivity(Intent(Settings.ACTION_SETTINGS))
        }
        button("Aggiornamenti SoIA") { status.text="Le app si aggiornano da Play Store e F-Droid. Per aggiornare l'immagine SoIA, collega il telefono via USB al PC e usa l'installatore con un'immagine verificata. Il prototipo non installa OTA automatici." }
        button("F-Droid: app e aggiornamenti open source") { launch("org.fdroid.fdroid") }
        button("Tutte le app installate") {
            val apps=PhoneTools(this).apps()
            AlertDialog.Builder(this).setTitle("App").setItems(apps.map { it.first }.toTypedArray()) { _,i -> launch(apps[i].second) }.show()
        }
        button("Agenda, note e profilo salvati") { task { store.records().entries.filter { listOf("agenda/","note/","profilo/").any { prefix -> it.key.startsWith(prefix) } }
            .joinToString("\n\n") { "${it.key}\n${it.value}" }.ifBlank { "Nessun dato. Chiedi a Nova di creare una nota o un promemoria." } } }
        button("Importa un altro modello GGUF") { choose(10,"*/*") }
        button("Verifica modelli e ripara download mancanti") { task {
            val models=ModelStore(this); models.downloadIfMissing(); models.verified("nova.gguf"); models.verified("whisper.bin"); models.voiceData(); models.status()+"\nImpronte verificate." } }
        button("Esporta backup di agenda, note e profilo") {
            AlertDialog.Builder(this).setMessage("Il file di backup contiene i dati in chiaro. Scegli un luogo privato.")
                .setPositiveButton("Esporta") { _,_ -> startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/json").putExtra(Intent.EXTRA_TITLE,"soia-backup.json"),11) }
                .setNegativeButton("Annulla",null).show()
        }
        button("Importa backup") { choose(12,"application/json") }
        button("Cancella cronologia conversazione") { AlertDialog.Builder(this).setMessage("Cancellare la conversazione sul telefono?")
            .setPositiveButton("Cancella") { _,_ -> task { Brain(this).clear(); "Cronologia cancellata." } }.setNegativeButton("Annulla",null).show() }
        button("Stato del collegamento e identità") { task { val pc=PcBridge(this)
            "PC: ${if(pc.isPaired()) pc.name() else "non abbinato"}\nIdentità sincronizzabile: ${store.identity.isReady()}\n${ModelStore(this).status()}" } }
    }
    private fun launch(pkg:String) { val intent=packageManager.getLaunchIntentForPackage(pkg)
        if(intent!=null) startActivity(intent) else status.text="L'app non ha una schermata avviabile o non è inclusa nell'immagine: $pkg" }
    private fun choose(code:Int,type:String) { startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType(type),code) }
    override fun onActivityResult(requestCode:Int,resultCode:Int,data:Intent?) {
        super.onActivityResult(requestCode,resultCode,data)
        if(resultCode!=RESULT_OK) return
        val uri=data?.data ?: return
        when(requestCode) {
            10 -> task { ModelStore(this).importModel(uri); "Modello GGUF importato e verificato." }
            11 -> task { contentResolver.openOutputStream(uri,"wt")!!.use { it.write(store.exportJson().toByteArray()) }; "Backup esportato." }
            12 -> task { val text=contentResolver.openInputStream(uri)!!.use { val bytes=it.readNBytes(4*1024*1024+1); require(bytes.size<=4*1024*1024); bytes.decodeToString() }
                "Importate ${store.importJson(text)} voci.".also { Reminders.schedule(this) } }
        }
    }
    override fun onRequestPermissionsResult(code:Int,permissions:Array<out String>,results:IntArray) {
        super.onRequestPermissionsResult(code,permissions,results)
        worker.execute { org.aios.nova.nearby.Nearby.start(this); Reminders.schedule(this) }
    }
    override fun onDestroy() { worker.shutdownNow(); super.onDestroy() }
}
