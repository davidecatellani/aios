package org.aios.nova.assistant

import android.content.Intent
import android.speech.RecognitionService
import android.speech.SpeechRecognizer

/** Richiesto da Android per un assistente. La dettatura vera (whisper.cpp) arriverà qui. */
class NovaRecognitionService : RecognitionService() {
    override fun onStartListening(intent: Intent, listener: Callback) {
        listener.error(SpeechRecognizer.ERROR_RECOGNIZER_BUSY)
    }

    override fun onCancel(listener: Callback) {}
    override fun onStopListening(listener: Callback) {}
}
