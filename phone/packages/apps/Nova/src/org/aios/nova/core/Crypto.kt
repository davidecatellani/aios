package org.aios.nova.core

import java.math.BigInteger
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.Signature
import java.security.spec.PKCS8EncodedKeySpec
import java.security.spec.X509EncodedKeySpec
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import org.json.JSONObject
import org.json.JSONArray

/** Formati interoperabili con ed25519.py, crypto.py e identity.py.
 * Ed25519 usa JCA quando disponibile; il fallback RFC 8032 è quello di riferimento
 * del progetto e non è a tempo costante. I semi sono cifrati da una chiave del Keystore Android.
 */
object Crypto {
    private val random = SecureRandom()
    private val zero = BigInteger.ZERO
    private val one = BigInteger.ONE
    private val p = one.shiftLeft(255) - BigInteger.valueOf(19)
    private val q = one.shiftLeft(252) + BigInteger("27742317777372353535851937790883648493")
    private val d = (-BigInteger.valueOf(121665) * BigInteger.valueOf(121666).modInverse(p)).mod(p)
    private val sqrtM1 = BigInteger.TWO.modPow((p - one) / BigInteger.valueOf(4), p)
    private data class Point(val x: BigInteger, val y: BigInteger, val z: BigInteger, val t: BigInteger)
    private val identity = Point(zero, one, one, zero)
    private val gy = (BigInteger.valueOf(4) * BigInteger.valueOf(5).modInverse(p)).mod(p)
    private val gx = recover(gy, false)!!
    private val base = Point(gx, gy, one, (gx * gy).mod(p))
    fun bytes(n: Int): ByteArray = ByteArray(n).also { random.nextBytes(it) }
    fun hash(data: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(data)
    fun hex(data: ByteArray): String = data.joinToString("") { "%02x".format(it) }
    private fun sha512(data: ByteArray) = MessageDigest.getInstance("SHA-512").digest(data)
    fun hmac(key: ByteArray, data: ByteArray): ByteArray = Mac.getInstance("HmacSHA256").run {
        init(SecretKeySpec(key, "HmacSHA256")); doFinal(data)
    }
    fun hkdf(key: ByteArray, info: ByteArray): ByteArray = hmac(hmac(ByteArray(32), key), info + byteArrayOf(1))
    private fun num(bytes: ByteArray) = BigInteger(1, bytes.reversedArray())
    private fun le(value: BigInteger, n: Int = 32): ByteArray {
        val raw = value.toByteArray().reversedArray()
        return ByteArray(n) { if (it < raw.size) raw[it] else 0 }
    }
    private fun add(a: Point, b: Point): Point {
        val aa = ((a.y - a.x) * (b.y - b.x)).mod(p)
        val bb = ((a.y + a.x) * (b.y + b.x)).mod(p)
        val cc = (BigInteger.TWO * a.t * b.t * d).mod(p)
        val dd = (BigInteger.TWO * a.z * b.z).mod(p)
        val e = bb - aa; val f = dd - cc; val g = dd + cc; val h = bb + aa
        return Point((e*f).mod(p), (g*h).mod(p), (f*g).mod(p), (e*h).mod(p))
    }
    private fun mul(scalar: BigInteger, initial: Point): Point {
        var scalarLeft = scalar; var point = initial; var result = identity
        while (scalarLeft > zero) {
            if (scalarLeft.testBit(0)) result = add(result, point)
            point = add(point, point); scalarLeft = scalarLeft.shiftRight(1)
        }
        return result
    }
    private fun equal(a: Point, b: Point) = (a.x*b.z-b.x*a.z).mod(p)==zero && (a.y*b.z-b.y*a.z).mod(p)==zero
    private fun recover(y: BigInteger, sign: Boolean): BigInteger? {
        if (y >= p) return null
        val x2 = ((y*y-one)*(d*y*y+one).mod(p).modInverse(p)).mod(p)
        if (x2 == zero) return if (sign) null else zero
        var x = x2.modPow((p+BigInteger.valueOf(3))/BigInteger.valueOf(8), p)
        if ((x*x-x2).mod(p)!=zero) x=(x*sqrtM1).mod(p)
        if ((x*x-x2).mod(p)!=zero) return null
        if (x.testBit(0)!=sign) x=p-x
        return x
    }
    private fun decode(raw: ByteArray): Point? {
        if (raw.size!=32) return null
        val encoded=num(raw); val y=encoded.clearBit(255)
        val x=recover(y, encoded.testBit(255)) ?: return null
        return Point(x,y,one,(x*y).mod(p))
    }
    private fun encode(point: Point): ByteArray {
        val inv=point.z.modInverse(p); val x=(point.x*inv).mod(p); var y=(point.y*inv).mod(p)
        if (x.testBit(0)) y=y.setBit(255)
        return le(y)
    }
    private fun expand(seed: ByteArray): Pair<BigInteger,ByteArray> {
        require(seed.size==32)
        val h=sha512(seed); val a=h.copyOfRange(0,32)
        a[0]=(a[0].toInt() and 248).toByte(); a[31]=((a[31].toInt() and 63) or 64).toByte()
        return num(a) to h.copyOfRange(32,64)
    }
    fun publicKey(seed: ByteArray): ByteArray = encode(mul(expand(seed).first,base))
    fun sign(seed: ByteArray,message: ByteArray): ByteArray {
        require(seed.size==32)
        try {
            val der=byteArrayOf(0x30,0x2e,0x02,0x01,0x00,0x30,0x05,0x06,0x03,0x2b,0x65,0x70,0x04,0x22,0x04,0x20)+seed
            val key=KeyFactory.getInstance("Ed25519").generatePrivate(PKCS8EncodedKeySpec(der))
            return Signature.getInstance("Ed25519").run { initSign(key); update(message); sign() }
        } catch (_: java.security.GeneralSecurityException) { }
        return signReference(seed,message)
    }
    internal fun signReference(seed:ByteArray,message:ByteArray):ByteArray {
        val (a,prefix)=expand(seed); val pub=publicKey(seed); val r=num(sha512(prefix+message)).mod(q)
        val rr=encode(mul(r,base)); val h=num(sha512(rr+pub+message)).mod(q)
        return rr+le((r+h*a).mod(q))
    }
    fun verify(pub: ByteArray,message: ByteArray,signature: ByteArray): Boolean {
        if (pub.size!=32 || signature.size!=64) return false
        try {
            val a=decode(pub) ?: return false; val r=decode(signature.copyOfRange(0,32)) ?: return false
            if (equal(a,identity) || !equal(mul(q,a),identity)) return false
            val s=num(signature.copyOfRange(32,64)); if(s>=q) return false
            val h=num(sha512(signature.copyOfRange(0,32)+pub+message)).mod(q)
            return equal(mul(s,base),add(r,mul(h,a)))
        } catch (_: ArithmeticException) { return false }
    }
    fun canonical(value: Any?): ByteArray = json(value).toByteArray(Charsets.UTF_8)
    private fun quote(s: String): String = buildString {
        append('"'); for(c in s) when(c) {
            '"' -> append("\\\""); '\\' -> append("\\\\"); '\b' -> append("\\b"); '\u000c' -> append("\\f")
            '\n' -> append("\\n"); '\r' -> append("\\r"); '\t' -> append("\\t")
            else -> if(c.code<32) append("\\u%04x".format(c.code)) else append(c)
        }; append('"')
    }
    private fun json(v: Any?): String = when(v) {
        null, JSONObject.NULL -> "null"
        is JSONObject -> v.keys().asSequence().toList().sorted().joinToString(",","{","}") { quote(it)+":"+json(v.get(it)) }
        is JSONArray -> (0 until v.length()).joinToString(",","[","]") { json(v.get(it)) }
        is String -> quote(v)
        is Boolean, is Number -> v.toString()
        else -> error("Tipo JSON non supportato")
    }
    private fun chacha(key: ByteArray, counter: Int, nonce: ByteArray): ByteArray {
        require(key.size==32 && nonce.size==12)
        fun word(a: ByteArray,i: Int)= (a[i].toInt() and 255) or ((a[i+1].toInt() and 255) shl 8) or
            ((a[i+2].toInt() and 255) shl 16) or ((a[i+3].toInt() and 255) shl 24)
        val original=intArrayOf(0x61707865,0x3320646e,0x79622d32,0x6b206574)+
            IntArray(8){word(key,it*4)}+intArrayOf(counter)+IntArray(3){word(nonce,it*4)}
        val s=original.copyOf()
        fun quarter(a:Int,b:Int,c:Int,d:Int) {
            s[a]+=s[b]; s[d]=Integer.rotateLeft(s[d] xor s[a],16)
            s[c]+=s[d]; s[b]=Integer.rotateLeft(s[b] xor s[c],12)
            s[a]+=s[b]; s[d]=Integer.rotateLeft(s[d] xor s[a],8)
            s[c]+=s[d]; s[b]=Integer.rotateLeft(s[b] xor s[c],7)
        }
        repeat(10) {
            quarter(0,4,8,12); quarter(1,5,9,13); quarter(2,6,10,14); quarter(3,7,11,15)
            quarter(0,5,10,15); quarter(1,6,11,12); quarter(2,7,8,13); quarter(3,4,9,14)
        }
        return ByteArray(64) { i -> ((s[i/4]+original[i/4]) ushr ((i%4)*8)).toByte() }
    }
    private fun stream(key:ByteArray,nonce:ByteArray,data:ByteArray): ByteArray {
        val out=ByteArray(data.size)
        for (offset in data.indices step 64) {
            val block=chacha(key,offset/64+1,nonce)
            for(i in 0 until minOf(64,data.size-offset)) out[offset+i]=(data[offset+i].toInt() xor block[i].toInt()).toByte()
        }
        return out
    }
    private fun tag(key:ByteArray,nonce:ByteArray,aad:ByteArray,ciphertext:ByteArray): ByteArray {
        val otk=chacha(key,0,nonce); val r=num(otk.copyOfRange(0,16)).and(BigInteger("0ffffffc0ffffffc0ffffffc0fffffff",16))
        val s=num(otk.copyOfRange(16,32)); val prime=one.shiftLeft(130)-BigInteger.valueOf(5)
        val mac=aad+ByteArray((16-aad.size%16)%16)+ciphertext+ByteArray((16-ciphertext.size%16)%16)+
            le(BigInteger.valueOf(aad.size.toLong()),8)+le(BigInteger.valueOf(ciphertext.size.toLong()),8)
        var acc=zero
        for(offset in mac.indices step 16) acc=((acc+num(mac.copyOfRange(offset,minOf(offset+16,mac.size))+byteArrayOf(1)))*r).mod(prime)
        return le((acc+s).and(one.shiftLeft(128)-one),16)
    }
    fun seal(key:ByteArray,plain:ByteArray,aad:ByteArray=byteArrayOf(),nonce:ByteArray=bytes(12)): ByteArray {
        val enc=stream(key,nonce,plain); return nonce+enc+tag(key,nonce,aad,enc)
    }
    fun open(key:ByteArray,box:ByteArray,aad:ByteArray=byteArrayOf()): ByteArray {
        require(box.size>=28) { "Dati cifrati incompleti" }
        val nonce=box.copyOfRange(0,12); val enc=box.copyOfRange(12,box.size-16)
        require(MessageDigest.isEqual(tag(key,nonce,aad,enc),box.copyOfRange(box.size-16,box.size))) { "Dati alterati o chiave errata" }
        return stream(key,nonce,enc)
    }
    fun unwrap(seed:ByteArray,eph:ByteArray,box:ByteArray,info:ByteArray):ByteArray {
        val scalar=expand(seed).first; val u=num(eph).clearBit(255)
        var x2=one; var z2=zero; var x3=u; var z3=one; var swap=false
        for(t in 254 downTo 0) {
            val bit=scalar.testBit(t)
            if(swap xor bit) { val x=x2; x2=x3; x3=x; val z=z2; z2=z3; z3=z }; swap=bit
            val a=(x2+z2).mod(p); val b=(x2-z2).mod(p); val aa=(a*a).mod(p); val bb=(b*b).mod(p); val e=(aa-bb).mod(p)
            val c=(x3+z3).mod(p); val dd=(x3-z3).mod(p); val da=(dd*a).mod(p); val cb=(c*b).mod(p)
            x3=((da+cb)*(da+cb)).mod(p); z3=(u*(da-cb)*(da-cb)).mod(p)
            x2=(aa*bb).mod(p); z2=(e*(aa+BigInteger.valueOf(121665)*e)).mod(p)
        }
        if(swap) { x2=x3; z2=z3 }
        val shared=le((x2*z2.modInverse(p)).mod(p))
        require(shared.any { it.toInt()!=0 }) { "Chiave X25519 non valida" }
        return open(hkdf(shared,"aios-avvolgi-v1".toByteArray()+info),box,info)
    }
}
