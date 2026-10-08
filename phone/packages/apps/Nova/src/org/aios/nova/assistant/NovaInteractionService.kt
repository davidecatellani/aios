package org.aios.nova.assistant

import android.service.voice.VoiceInteractionService

/** Assistente scelto dall'utente; il wake software è separato e facoltativo. */
class NovaInteractionService : VoiceInteractionService() {
    override fun onReady() { super.onReady(); active=this }
    override fun onShutdown() { if(active===this) active=null; super.onShutdown() }
    companion object { @Volatile var active:NovaInteractionService?=null }
}
