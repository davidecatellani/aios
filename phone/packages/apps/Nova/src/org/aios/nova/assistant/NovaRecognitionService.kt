package org.aios.nova.assistant

import android.content.Intent
import android.speech.RecognitionService
import android.speech.SpeechRecognizer
import android.os.Bundle
import java.util.concurrent.Executors

/** Dettatura offline per Nova e per le app che scelgono questo servizio. */
class NovaRecognitionService : RecognitionService() {
    private val worker=Executors.newSingleThreadExecutor()
    private val voice by lazy { OfflineVoice(applicationContext) }
    @Volatile private var active=false
    override fun onStartListening(intent: Intent, listener: Callback) {
        if(active) { listener.error(SpeechRecognizer.ERROR_RECOGNIZER_BUSY); return }
        active=true
        worker.execute {
            try {
                val text=voice.listen { listener.readyForSpeech(Bundle()); listener.beginningOfSpeech() }
                listener.endOfSpeech()
                if(text.isBlank()) listener.error(SpeechRecognizer.ERROR_NO_MATCH)
                else listener.results(Bundle().apply { putStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION,arrayListOf(text)) })
            } catch(e:Exception) { runCatching { listener.error(SpeechRecognizer.ERROR_CLIENT) } }
            finally { active=false }
        }
    }
    override fun onCancel(listener: Callback) { voice.cancel() }
    override fun onStopListening(listener: Callback) { voice.stopListening() }
    override fun onDestroy() { voice.cancel(); worker.shutdownNow(); super.onDestroy() }
}
