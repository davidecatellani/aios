package org.aios.nova.setup

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import org.aios.nova.R
import org.aios.nova.pc.PcBridge
import java.io.File

/**
 * Primo avvio: «Ripristina dal computer». Se l'installatore del PC ha lasciato l'indirizzo del QR
 * (Android/data/org.aios.nova/files/abbina.txt), il collegamento è automatico; altrimenti si incolla.
 */
class RestoreActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val bridge = PcBridge(applicationContext)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(48, 48, 48, 48) }
        val status = TextView(this).apply { text = getString(R.string.restore_text); textSize = 17f }
        val input = EditText(this).apply { hint = "https://…#abbina=…" }
        val go = Button(this).apply { text = getString(R.string.restore_title) }
        root.addView(status); root.addView(input); root.addView(go)
        setContentView(root)

        val fromPc = File(getExternalFilesDir(null), "abbina.txt")
        if (fromPc.exists()) input.setText(fromPc.readText().trim())
        go.setOnClickListener {
            val qr = input.text.toString().trim()
            if (qr.isEmpty()) return@setOnClickListener
            go.isEnabled = false
            Thread {
                val message = try {
                    val name = bridge.pair(qr)
                    org.aios.nova.nearby.Nearby.start(applicationContext)
                    fromPc.delete()  // solo dopo l'abbinamento riuscito: un errore di rete deve essere riprovabile
                    val sync=runCatching { org.aios.nova.core.PrototypeStore(applicationContext).sync() }
                        .getOrElse { "Sincronizzazione: ${it.message}" }
                    org.aios.nova.core.Reminders.schedule(applicationContext)
                    "Collegato a $name.\n$sync"
                } catch (e: Exception) { "Non riuscito: ${e.message}" }
                runOnUiThread {
                    if (!isFinishing && !isDestroyed) {
                        status.text = message
                        go.isEnabled = true
                    }
                }
            }.start()
        }
    }
}
