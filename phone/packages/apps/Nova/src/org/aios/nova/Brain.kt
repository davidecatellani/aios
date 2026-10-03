package org.aios.nova

import android.content.Context
import org.aios.nova.llm.LocalModel
import org.aios.nova.pc.PcBridge

/**
 * Chi risponde: il PC quando è vicino (modello più grande, non consuma la batteria del telefono),
 * altrimenti il modello del telefono, avviato solo per questa domanda.
 */
class Brain(private val context: Context) {
    private val pc = PcBridge(context)
    private val local = LocalModel(context)

    fun ask(text: String): String {
        if (pc.isPaired()) {
            try {
                return pc.ask(text)
            } catch (e: Exception) {
                // PC lontano o spento: si prosegue con il modello del telefono
            }
        }
        if (local.isInstalled()) {
            return local.complete(text)
        }
        return if (pc.isPaired())
            "Il computer non è raggiungibile e sul telefono non c'è ancora un modello AI: lo scarico la prossima volta che è in carica e sul Wi-Fi."
        else
            "Collega il telefono al computer («Nova, collega il telefono» sul PC) per usare il suo modello AI."
    }
}
