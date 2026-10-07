package org.aios.nova

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.util.concurrent.Executors
import org.aios.nova.pc.PcConfirmation
import org.aios.nova.pc.PcDecision
import org.aios.nova.setup.RestoreActivity
import org.aios.nova.setup.PrototypeActivity
import org.aios.nova.assistant.OfflineVoice
import org.aios.nova.assistant.WakeService
import android.Manifest
import android.content.pm.PackageManager

/** La conversazione con Nova (interfaccia essenziale, senza librerie esterne). */
class NovaActivity : Activity() {
    private val worker = Executors.newSingleThreadExecutor()
    private lateinit var log: TextView
    private lateinit var conversation:LinearLayout
    @Volatile private var closed = false
    @Volatile private var decision: PcDecision? = null
    private var confirmationDialog: AlertDialog? = null
    private var busy = false
    private lateinit var input:EditText
    private lateinit var send:Button
    private lateinit var pair:Button
    private lateinit var mic:Button
    private val voice by lazy { OfflineVoice(applicationContext) }
    private val brain by lazy { Brain(applicationContext) }
    private var pendingWake:String?=null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, pad, pad, pad) }
        log = TextView(this).apply { text = getString(R.string.hello); textSize = 17f }
        conversation=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL; addView(log) }
        val scroll = ScrollView(this).apply { addView(conversation) }
        input = EditText(this).apply { hint = getString(R.string.hint); imeOptions = EditorInfo.IME_ACTION_SEND; isSingleLine = true }
        send = Button(this).apply { text = getString(R.string.send) }
        pair = Button(this).apply { text = getString(R.string.connect_pc) }
        mic=Button(this).apply { text="Parla / termina dettatura" }
        val settings=Button(this).apply { text="Prototipo: app, voce, dati e PC"; setOnClickListener { startActivity(Intent(this@NovaActivity,PrototypeActivity::class.java)) } }
        pair.setOnClickListener { startActivity(Intent(this, RestoreActivity::class.java)) }
        root.addView(scroll, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        root.addView(input)
        root.addView(send)
        root.addView(mic)
        root.addView(pair)
        root.addView(settings)
        setContentView(root)

        fun submit() {
            val text = input.text.toString().trim()
            if (text.isEmpty() || busy) return
            busy = true
            send.isEnabled = false
            pair.isEnabled = false
            input.setText("")
            log.append("\n\n› $text")
            worker.execute {
                val answer = try { brain.ask(text, ::confirmOnPhone,::attachFile) } catch (e: Exception) { "Qualcosa è andato storto: ${e.message}" }
                runOnUiThread {
                    if (!closed) {
                        log.append("\n$answer")
                        busy = false
                        send.isEnabled = true
                        pair.isEnabled = true
                    }
                }
                if(!closed && getSharedPreferences("prototype",MODE_PRIVATE).getBoolean("speak",false)) runCatching { voice.speak(answer) }
            }
        }
        send.setOnClickListener { submit() }
        input.setOnEditorActionListener { _, _, _ -> submit(); true }
        mic.setOnClickListener {
            if(busy) { voice.stopListening(); return@setOnClickListener }
            if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED) {
                requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO),31); return@setOnClickListener
            }
            busy=true; send.isEnabled=false; pair.isEnabled=false
            log.append("\n🎙 Parla: la registrazione resta sul telefono.")
            worker.execute {
                val text=runCatching { voice.listen() }.getOrElse { "" }
                runOnUiThread { if(!closed) { busy=false; send.isEnabled=true; pair.isEnabled=true
                    if(text.isBlank()) log.append("\nNon ho riconosciuto la voce. Controlla microfono e modello vocale nelle impostazioni.")
                    else { input.setText(text); submit() } } }
            }
        }
        pendingWake=WakeService.consume(intent)
        pendingWake?.let { input.setText(it); pendingWake=null; submit() }
    }

    private fun attachFile(name:String,url:String) { runOnUiThread { if(!closed) conversation.addView(Button(this).apply {
        text="Salva dal PC: $name"
        setOnClickListener { startActivity(Intent(this@NovaActivity,org.aios.nova.pc.PcFilesActivity::class.java).putExtra("pc_ticket",url).putExtra("pc_name",name)) }
    }) } }

    override fun onNewIntent(intent:Intent) { super.onNewIntent(intent); setIntent(intent)
        WakeService.consume(intent)?.let { input.setText(it); if(!busy) send.performClick() } }
    override fun onResume() { super.onResume(); WakeService.paused=true }
    override fun onPause() { WakeService.paused=false; super.onPause() }

    /** Il dialogo vive sul thread UI; la richiesta di rete aspetta sul worker. */
    private fun confirmOnPhone(request: PcConfirmation, timeoutMs: Long): Boolean {
        val pending = PcDecision()
        decision = pending
        runOnUiThread {
            if (closed || isFinishing || isDestroyed) {
                pending.answer(false)
            } else {
                val message = listOfNotNull(request.label, request.warning).joinToString("\n\n")
                confirmationDialog = AlertDialog.Builder(this)
                    .setTitle(R.string.confirm_pc_action)
                    .setMessage(message)
                    .setPositiveButton(R.string.confirm) { _, _ -> pending.answer(true) }
                    .setNegativeButton(R.string.cancel) { _, _ -> pending.answer(false) }
                    .setOnCancelListener { pending.answer(false) }
                    .create().also { it.show() }
            }
        }
        return try { pending.await(timeoutMs) } finally {
            runOnUiThread {
                confirmationDialog?.dismiss()
                confirmationDialog = null
                if (decision === pending) decision = null
            }
        }
    }

    override fun onDestroy() {
        closed = true
        decision?.answer(false)
        confirmationDialog?.dismiss()
        worker.shutdownNow()
        voice.cancel()
        super.onDestroy()
    }
}
