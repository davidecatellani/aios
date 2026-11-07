package org.aios.nova.core

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Chiavi di abbinamento, identità e sincronizzazione cifrate con una chiave del Keystore. */
class Secrets(context:Context) {
    private val prefs=context.getSharedPreferences("secrets",Context.MODE_PRIVATE)
    private val alias="org.aios.nova.secrets.v1"
    private fun key():SecretKey = synchronized(LOCK) {
        val store=KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias,null) as? SecretKey) ?: KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore").run {
            init(KeyGenParameterSpec.Builder(alias,KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build())
            generateKey()
        }
    }
    fun get(name:String):ByteArray? {
        val encoded=prefs.getString(name,null) ?: return null
        val box=Base64.getDecoder().decode(encoded)
        require(box.size>=28)
        return Cipher.getInstance("AES/GCM/NoPadding").run {
            init(Cipher.DECRYPT_MODE,key(),GCMParameterSpec(128,box.copyOfRange(0,12)))
            updateAAD(name.toByteArray()); doFinal(box.copyOfRange(12,box.size))
        }
    }
    fun set(name:String,value:ByteArray) {
        val cipher=Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE,key()); updateAAD(name.toByteArray()) }
        val encrypted=cipher.doFinal(value)
        check(prefs.edit().putString(name,Base64.getEncoder().encodeToString(cipher.iv+encrypted)).commit()) { "Impossibile conservare la chiave" }
    }
    fun remove(name:String) { prefs.edit().remove(name).commit() }
    companion object { private val LOCK=Any() }
}
