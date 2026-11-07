package org.aios.nova.pc

import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Una sola risposta: chiusura, timeout e interruzione valgono sempre come annullamento. */
class PcDecision {
    private val latch = CountDownLatch(1)
    private var answered = false
    private var approved = false

    @Synchronized
    fun answer(ok: Boolean) {
        if (answered) return
        answered = true
        approved = ok
        latch.countDown()
    }

    fun await(timeoutMs: Long): Boolean {
        try {
            if (!latch.await(timeoutMs.coerceAtLeast(0), TimeUnit.MILLISECONDS)) {
                answer(false)
                return false
            }
        } catch (e: InterruptedException) {
            answer(false)
            Thread.currentThread().interrupt()
            return false
        }
        return synchronized(this) { approved }
    }
}
