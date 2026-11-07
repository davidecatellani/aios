package org.aios.nova.core

import java.io.File
import java.io.InputStream
import java.security.MessageDigest
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/** Download/importazione atomici: una prova fallita conserva il modello precedente. */
object VerifiedFiles {
    fun sha256(file: File): String = file.inputStream().use { input ->
        val digest = MessageDigest.getInstance("SHA-256")
        val block = ByteArray(128 * 1024)
        while (true) { val n = input.read(block); if (n < 0) break; digest.update(block, 0, n) }
        digest.digest().joinToString("") { "%02x".format(it) }
    }

    fun install(input: InputStream, dest: File, expected: String?, size: Long?, max: Long,
                magic: ByteArray? = null, progress: (Long) -> Unit = {}): String {
        require(max > 0 && (size == null || size in 1..max))
        require(expected == null || expected.matches(Regex("[0-9a-f]{64}")))
        dest.parentFile!!.mkdirs()
        val temp = File.createTempFile(".download-", ".tmp", dest.parentFile)
        try {
            val digest = MessageDigest.getInstance("SHA-256")
            var total = 0L
            temp.outputStream().use { output ->
                val block = ByteArray(128 * 1024)
                while (true) {
                    if (Thread.currentThread().isInterrupted) throw InterruptedException("Download annullato")
                    val n = input.read(block)
                    if (n < 0) break
                    total += n
                    require(total <= max && (size == null || total <= size)) { "File più grande del previsto" }
                    output.write(block, 0, n); digest.update(block, 0, n); progress(total)
                }
            }
            require(total > 0 && (size == null || total == size)) { "File incompleto" }
            val hash = digest.digest().joinToString("") { "%02x".format(it) }
            require(expected == null || hash == expected) { "Impronta SHA-256 non valida" }
            if (magic != null) temp.inputStream().use { stream ->
                val found = ByteArray(magic.size)
                require(stream.read(found) == found.size && found.contentEquals(magic)) { "Formato modello non valido" }
            }
            Files.move(temp.toPath(), dest.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            return hash
        } finally { temp.delete() }
    }
}
