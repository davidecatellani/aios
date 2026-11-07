package org.aios.nova.core

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.provider.AlarmClock
import android.media.AudioManager
import android.hardware.camera2.CameraManager
import org.aios.nova.pc.PcConfirmation
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import java.time.LocalDateTime

/** Operazioni tipizzate; nessun comando shell, URL arbitrario o invio SMS automatico. */
class PhoneTools(private val context:Context) {
    private val store=PrototypeStore(context)
    private val operations=linkedMapOf(
        "apri_app" to "Apri un'app installata: testo è nome o pacchetto",
        "impostazioni" to "Apri impostazioni: testo è wifi, bluetooth, schermo, suono o generale",
        "chiama" to "Apri il numero nel dialer: testo è numero telefonico",
        "sms" to "Prepara SMS da inviare manualmente: testo è numero, dettaglio è messaggio",
        "cerca_web" to "Apri ricerca web: testo è ricerca",
        "volume" to "Regola volume multimedia: numero è percentuale 0..100",
        "torcia" to "Accendi o spegni torcia: numero 1 o 0",
        "timer" to "Prepara timer: numero è durata in secondi, testo è etichetta",
        "nota" to "Conserva nota offline cifrata e sincronizzabile: testo è nota",
        "promemoria" to "Crea promemoria: testo è titolo, dettaglio è data ISO locale yyyy-MM-ddTHH:mm",
        "evento" to "Crea evento agenda: testo è titolo, dettaglio è inizio ISO locale yyyy-MM-ddTHH:mm, numero è durata in minuti (default 60)",
        "profilo" to "Memorizza informazione personale: testo è chiave, dettaglio è valore",
        "elenca" to "Leggi dati salvati: testo è note, agenda, profilo oppure app",
        "cancella" to "Elimina voce salvata: testo è identificatore esatto restituito da elenca"
    )
    fun schema():JSONArray=JSONArray().put(JSONObject().put("type","function").put("function",JSONObject()
        .put("name","telefono").put("description",operations.entries.joinToString("; ") { "${it.key}: ${it.value}" })
        .put("parameters",JSONObject().put("type","object").put("properties",JSONObject()
            .put("azione",JSONObject().put("type","string").put("enum",JSONArray(operations.keys)))
            .put("testo",JSONObject().put("type","string"))
            .put("dettaglio",JSONObject().put("type","string"))
            .put("numero",JSONObject().put("type","integer")))
            .put("required",JSONArray().put("azione")).put("additionalProperties",false))))
    fun direct(text:String):JSONObject? {
        val s=text.trim(); val lower=s.lowercase()
        fun command(action:String,value:String)=JSONObject().put("azione",action).put("testo",value)
        for((prefix,action) in listOf("apri " to "apri_app","chiama " to "chiama","cerca sul web " to "cerca_web","nota " to "nota","annota " to "nota"))
            if(lower.startsWith(prefix)) return command(action,s.substring(prefix.length))
        if(lower in listOf("accendi torcia","spegni torcia")) return command("torcia","").put("numero",if(lower.startsWith("accendi")) 1 else 0)
        if(lower in listOf("mostra note","mostra agenda","mostra profilo","mostra app")) return command("elenca",lower.removePrefix("mostra "))
        if(lower.startsWith("impostazioni")) return command("impostazioni",lower.removePrefix("impostazioni").trim())
        return null
    }
    fun apps():List<Pair<String,String>> = context.packageManager.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER),0)
        .map { it.loadLabel(context.packageManager).toString() to it.activityInfo.packageName }.distinctBy { it.second }.sortedBy { it.first.lowercase() }
    private fun open(intent:Intent) { context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
    fun execute(args:JSONObject,confirm:(PcConfirmation,Long)->Boolean):String {
        val action=args.getString("azione"); require(action in operations) { "Operazione del telefono non consentita" }
        val text=args.optString("testo").trim(); val detail=args.optString("dettaglio").trim(); val number=args.optInt("numero")
        require(text.length<=8000 && detail.length<=8000)
        if(action=="elenca") {
            if(text=="app") return apps().joinToString("\n") { "${it.first} (${it.second})" }
            val prefix=mapOf("note" to "note/","agenda" to "agenda/","profilo" to "profilo/")[text] ?: error("Scegli note, agenda, profilo o app")
            return store.records(prefix).entries.joinToString("\n") { "${it.key}: ${it.value}" }.ifBlank { "Nessuna voce salvata." }
        }
        if(!confirm(PcConfirmation("Telefono: ${operations[action]}\n${text.take(500)} ${detail.take(500)}${if(args.has("numero")) " ($number)" else ""}",null),120_000)) return "Operazione annullata."
        when(action) {
            "apri_app" -> {
                val match=apps().filter { it.second==text || it.first.equals(text,true) }
                require(match.size==1) { "App non trovata o nome ambiguo: usa «mostra app»." }
                open(context.packageManager.getLaunchIntentForPackage(match.single().second) ?: error("App non avviabile"))
            }
            "impostazioni" -> open(Intent(mapOf("wifi" to Settings.ACTION_WIFI_SETTINGS,"bluetooth" to Settings.ACTION_BLUETOOTH_SETTINGS,
                "schermo" to Settings.ACTION_DISPLAY_SETTINGS,"suono" to Settings.ACTION_SOUND_SETTINGS)[text.lowercase()] ?: Settings.ACTION_SETTINGS))
            "chiama","sms" -> {
                require(text.matches(Regex("[+0-9 ()-]{3,40}"))) { "Numero non valido" }
                if(action=="chiama") open(Intent(Intent.ACTION_DIAL,Uri.fromParts("tel",text,null)))
                else open(Intent(Intent.ACTION_SENDTO,Uri.fromParts("smsto",text,null)).putExtra("sms_body",detail))
            }
            "cerca_web" -> { require(text.isNotEmpty()); open(Intent(Intent.ACTION_VIEW,Uri.parse("https://duckduckgo.com/?q=${Uri.encode(text)}"))) }
            "volume" -> { require(number in 0..100); val audio=context.getSystemService(AudioManager::class.java) ?: error("Audio non disponibile")
                audio.setStreamVolume(AudioManager.STREAM_MUSIC,audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)*number/100,AudioManager.FLAG_SHOW_UI) }
            "torcia" -> { require(number in 0..1); val camera=context.getSystemService(CameraManager::class.java) ?: error("Fotocamera non disponibile")
                val id=camera.cameraIdList.firstOrNull { camera.getCameraCharacteristics(it).get(android.hardware.camera2.CameraCharacteristics.FLASH_INFO_AVAILABLE)==true } ?: error("Torcia non disponibile")
                camera.setTorchMode(id,number==1) }
            "timer" -> { require(number in 1..86400); open(Intent(AlarmClock.ACTION_SET_TIMER).putExtra(AlarmClock.EXTRA_LENGTH,number).putExtra(AlarmClock.EXTRA_MESSAGE,text)) }
            "nota" -> { require(text.isNotEmpty()); store.set("note/${UUID.randomUUID()}",JSONObject().put("text",text).put("created",LocalDateTime.now().toString())) }
            "promemoria" -> { require(text.isNotEmpty()); val due=LocalDateTime.parse(detail); require(due.isAfter(LocalDateTime.now())) { "Scegli una data futura" }
                store.set("agenda/${UUID.randomUUID()}",JSONObject().put("kind","reminder").put("title",text).put("due",due.toString()).put("done",false).put("source","Nova"))
                Reminders.schedule(context) }
            "evento" -> { require(text.isNotEmpty()); val start=LocalDateTime.parse(detail); val minutes=if(args.has("numero")) number else 60
                require(minutes in 1..10080)
                store.set("agenda/${UUID.randomUUID()}",JSONObject().put("kind","event").put("title",text).put("start",start.toString())
                    .put("end",start.plusMinutes(minutes.toLong()).toString()).put("all_day",false).put("location","").put("notes","").put("source","Nova")) }
            "profilo" -> { require(text.matches(Regex("[A-Za-z0-9_.-]{1,64}")) && detail.isNotEmpty()); store.set("profilo/$text",detail) }
            "cancella" -> { require(store.records().containsKey(text) && listOf("note/","agenda/","profilo/").any { text.startsWith(it) }); store.set(text,null); Reminders.schedule(context) }
        }
        return "Operazione completata."
    }
}
