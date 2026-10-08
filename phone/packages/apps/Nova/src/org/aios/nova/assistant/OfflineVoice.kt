package org.aios.nova.assistant

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.MediaPlayer
import org.aios.nova.llm.ModelStore
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.TimeUnit

/** Audio mai inviato in rete; una registrazione alla volta, durata e processi limitati. */
class OfflineVoice(private val context:Context) {
    @Volatile private var stop=false
    @Volatile private var cancelled=false
    @Volatile private var process:Process?=null
    fun stopListening() { stop=true }
    fun cancel() { cancelled=true; stop=true; process?.destroy() }
    fun listen(maxSeconds:Int=20, ready:()->Unit={}):String=synchronized(MIC) {
        stop=false; cancelled=false
        val model=ModelStore(context).verified("whisper.bin")
        val minimum=AudioRecord.getMinBufferSize(16000,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT)
        require(minimum>0) { "Il microfono non supporta la registrazione a 16 kHz" }
        val audio=AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION,16000,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT,maxOf(minimum,4096))
        val samples=java.io.ByteArrayOutputStream(); val buffer=ShortArray(1024)
        var voiced=false; var silence=0; var count=0
        try {
            check(audio.state==AudioRecord.STATE_INITIALIZED) { "Microfono non disponibile" }
            audio.startRecording(); ready()
            while(!stop && !Thread.currentThread().isInterrupted && count<16000*maxSeconds) {
                val n=audio.read(buffer,0,buffer.size)
                check(n>0) { "Registrazione interrotta" }
                val loud=buffer.take(n).any { kotlin.math.abs(it.toInt())>650 }
                if(loud) { voiced=true; silence=0 } else silence+=n
                for(i in 0 until n) { samples.write(buffer[i].toInt() and 255); samples.write(buffer[i].toInt() shr 8 and 255) }
                count+=n
                if(voiced && silence>16000 && count>16000) break
            }
        } finally { runCatching { audio.stop() }; audio.release() }
        if(cancelled || Thread.currentThread().isInterrupted) throw InterruptedException("Dettatura annullata")
        if(!voiced) return ""
        val dir=File(context.cacheDir,"voce").apply { mkdirs() }; val wav=File(dir,"input.wav"); val output=File(dir,"dettato")
        try {
            wave(wav,samples.toByteArray(),16000)
            val p=ProcessBuilder("/system_ext/bin/aios-whisper","-m",model.path,"-f",wav.path,"-l","it","-t","4","-nt","-otxt","-of",output.path)
                .redirectErrorStream(true).redirectOutput(File(dir,"whisper.log")).start()
            process=p
            check(p.waitFor(90,TimeUnit.SECONDS) && p.exitValue()==0) { "Trascrizione offline non riuscita" }
            if(cancelled) throw InterruptedException("Dettatura annullata")
            return File(output.path+".txt").readText().trim().take(8000)
        } finally { process?.destroy(); process=null; wav.delete(); File(output.path+".txt").delete(); File(dir,"whisper.log").delete() }
    }
    fun speak(text:String)=synchronized(SPEAKER) {
        val data=ModelStore(context).voiceData()
        val dir=File(context.cacheDir,"voce").apply { mkdirs() }; val input=File(dir,"risposta.txt"); val output=File(dir,"risposta.wav")
        var player:MediaPlayer?=null
        var speech:Process?=null
        try {
            input.writeText(text.take(4000))
            val p=ProcessBuilder("/system_ext/bin/aios-espeak","-v","it","-s","165","-f",input.path,"-w",output.path)
                .also { it.environment()["ESPEAK_DATA_PATH"]=data.path }
                .redirectErrorStream(true).redirectOutput(File(dir,"espeak.log")).start()
            speech=p
            check(p.waitFor(20,TimeUnit.SECONDS) && p.exitValue()==0) { p.destroy(); "Sintesi vocale non riuscita" }
            player=MediaPlayer().apply { setDataSource(output.path); prepare(); start() }
            while(player.isPlaying) { if(Thread.currentThread().isInterrupted) throw InterruptedException(); Thread.sleep(100) }
        } finally { speech?.destroy(); player?.release(); input.delete(); output.delete(); File(dir,"espeak.log").delete() }
    }
    companion object {
        private val MIC=Any(); private val SPEAKER=Any()
        fun wave(file:File,pcm:ByteArray,rate:Int) {
            val h=ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
            h.put("RIFF".toByteArray()).putInt(pcm.size+36).put("WAVEfmt ".toByteArray()).putInt(16).putShort(1).putShort(1)
                .putInt(rate).putInt(rate*2).putShort(2).putShort(16).put("data".toByteArray()).putInt(pcm.size)
            file.outputStream().use { it.write(h.array()); it.write(pcm) }
        }
    }
}
