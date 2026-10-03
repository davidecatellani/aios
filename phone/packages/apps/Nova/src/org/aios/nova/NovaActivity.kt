package org.aios.nova

import android.app.Activity
import android.os.Bundle
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.util.concurrent.Executors

/** La conversazione con Nova (interfaccia essenziale, senza librerie esterne). */
class NovaActivity : Activity() {
    private val worker = Executors.newSingleThreadExecutor()
    private lateinit var log: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, pad, pad, pad) }
        log = TextView(this).apply { text = getString(R.string.hello); textSize = 17f }
        val scroll = ScrollView(this).apply { addView(log) }
        val input = EditText(this).apply { hint = getString(R.string.hint); imeOptions = EditorInfo.IME_ACTION_SEND; isSingleLine = true }
        val send = Button(this).apply { text = getString(R.string.send) }
        root.addView(scroll, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        root.addView(input)
        root.addView(send)
        setContentView(root)

        val brain = Brain(applicationContext)
        fun submit() {
            val text = input.text.toString().trim()
            if (text.isEmpty()) return
            input.setText("")
            log.append("\n\n› $text")
            worker.execute {
                val answer = try { brain.ask(text) } catch (e: Exception) { "Qualcosa è andato storto: ${e.message}" }
                runOnUiThread { log.append("\n$answer") }
            }
        }
        send.setOnClickListener { submit() }
        input.setOnEditorActionListener { _, _, _ -> submit(); true }
    }

    override fun onDestroy() {
        worker.shutdown()
        super.onDestroy()
    }
}
