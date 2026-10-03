package org.aios.nova.assistant

import android.service.voice.VoiceInteractionService

/** Registra Nova come assistente di sistema. La parola «Ehi Nova» arriverà sul DSP audio (non sulla CPU). */
class NovaInteractionService : VoiceInteractionService()
