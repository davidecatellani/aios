package org.aios.nova.nearby

import android.util.Base64
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * I codici BLE con cui telefono e PC si riconoscono (identici a copilot/aios_copilot/mesh/nearby.py):
 * 11 byte = versione, ruolo, flag, HMAC-SHA256(segreto, "aios-beacon" ‖ finestra ‖ ruolo ‖ flag)[0..8].
 * La finestra cambia ogni 15 minuti: gli estranei vedono numeri sempre diversi.
 *
 * Valori di prova (gli stessi di copilot/tests/test_nearby.py): con segreto = hkdf(0xab×32, "aios-vicino-v1")
 * make(segreto, ROLE_PC, F_RETE, 1800000000) = 0100029f0d5a6712816cbc e
 * network(segreto, 1800000000) = ("AIOS-a66541", "4LAHyg7kCQV1ZzAoX8Xd").
 */
object NearbyCode {
    const val COMPANY = 0xFFFF
    const val VERSION = 1
    const val WINDOW = 900L
    const val ROLE_PC = 0
    const val ROLE_PHONE = 1
    const val F_INTERNET = 1
    const val F_RETE = 2
    const val F_CHIEDE_INTERNET = 4
    const val F_CHIEDE_BT = 8
    const val F_BATTERIA_BASSA = 16
    const val F_IN_CARICA = 32

    /** Dalla chiave ricevuta all'abbinamento: il PC ne conserva solo lo sha256 e calcola lo stesso segreto. */
    fun secretFromKey(key: String): ByteArray {
        val hash = MessageDigest.getInstance("SHA-256").digest(key.toByteArray())
        return hkdf(hash, "aios-vicino-v1".toByteArray(), 32)
    }

    fun hkdf(key: ByteArray, info: ByteArray, length: Int): ByteArray {
        val prk = hmac(ByteArray(32), key)
        val out = java.io.ByteArrayOutputStream()
        var block = ByteArray(0)
        var i = 1
        while (out.size() < length) {
            block = hmac(prk, block + info + byteArrayOf(i.toByte()))
            out.write(block)
            i++
        }
        return out.toByteArray().copyOf(length)
    }

    private fun hmac(key: ByteArray, data: ByteArray): ByteArray =
        Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(key, "HmacSHA256")) }.doFinal(data)

    private fun mac(secret: ByteArray, window: Long, role: Int, flags: Int): ByteArray {
        val w = java.nio.ByteBuffer.allocate(8).putLong(window).array()
        return hmac(secret, "aios-beacon".toByteArray() + w + byteArrayOf(role.toByte(), flags.toByte())).copyOf(8)
    }

    fun make(secret: ByteArray, role: Int, flags: Int, nowSec: Long): ByteArray =
        byteArrayOf(VERSION.toByte(), role.toByte(), flags.toByte()) + mac(secret, nowSec / WINDOW, role, flags and 0xFF)

    /** → flag del PC, o null se il codice non è di un nostro dispositivo. */
    fun readPc(data: ByteArray, secret: ByteArray, nowSec: Long): Int? {
        if (data.size != 11 || data[0].toInt() != VERSION || data[1].toInt() != ROLE_PC) return null
        val flags = data[2].toInt() and 0xFF
        val got = data.copyOfRange(3, 11)
        val w = nowSec / WINDOW
        for (window in longArrayOf(w, w - 1, w + 1)) {
            if (MessageDigest.isEqual(mac(secret, window, ROLE_PC, flags), got)) return flags
        }
        return null
    }

    /** Nome e password della rete diretta: gli stessi che calcola il PC, cambiano ogni giorno. */
    fun network(secret: ByteArray, nowSec: Long, dayOffset: Int = 0): Pair<String, String> {
        val day = (nowSec / 86400 + dayOffset).toInt()
        val info = "aios-vicino-rete".toByteArray() + java.nio.ByteBuffer.allocate(4).putInt(day).array()
        val d = hkdf(secret, info, 24)
        val ssid = "AIOS-" + d.copyOfRange(0, 3).joinToString("") { "%02x".format(it) }
        val password = Base64.encodeToString(d.copyOfRange(3, 18), Base64.URL_SAFE or Base64.NO_WRAP)
        return ssid to password
    }
}
