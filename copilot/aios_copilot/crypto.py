"""Cifratura autenticata ChaCha20-Poly1305 (RFC 8439) e HKDF-SHA256 (RFC 5869).

Senza dipendenze: se c'è la libreria `cryptography` la si usa (molto più veloce),
altrimenti l'implementazione in Python puro qui sotto, verificata con i vettori delle RFC.
Serve alla sincronizzazione cifrata end-to-end tra i dispositivi (sync.py).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct


class DecryptError(ValueError):
    pass


def hkdf(key: bytes, info: bytes, length: int = 32, salt: bytes = b"") -> bytes:
    prk = hmac.new(salt or bytes(32), key, hashlib.sha256).digest()
    out, block = b"", b""
    for i in range(1, -(-length // 32) + 1):
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
    return out[:length]


def _rotl(v: int, c: int) -> int:
    return ((v << c) & 0xFFFFFFFF) | (v >> (32 - c))


def _quarter(s: list[int], a: int, b: int, c: int, d: int) -> None:
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF; s[d] = _rotl(s[d] ^ s[a], 16)  # noqa: E702
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF; s[b] = _rotl(s[b] ^ s[c], 12)  # noqa: E702
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF; s[d] = _rotl(s[d] ^ s[a], 8)  # noqa: E702
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF; s[b] = _rotl(s[b] ^ s[c], 7)  # noqa: E702


def _block(key: bytes, counter: int, nonce: bytes) -> bytes:
    state = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574, *struct.unpack("<8I", key), counter,
             *struct.unpack("<3I", nonce)]
    s = list(state)
    for _ in range(10):
        _quarter(s, 0, 4, 8, 12); _quarter(s, 1, 5, 9, 13); _quarter(s, 2, 6, 10, 14); _quarter(s, 3, 7, 11, 15)  # noqa: E702
        _quarter(s, 0, 5, 10, 15); _quarter(s, 1, 6, 11, 12); _quarter(s, 2, 7, 8, 13); _quarter(s, 3, 4, 9, 14)  # noqa: E702
    return struct.pack("<16I", *((a + b) & 0xFFFFFFFF for a, b in zip(s, state)))


def chacha20(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 64):
        stream = _block(key, counter + i // 64, nonce)
        out += bytes(x ^ y for x, y in zip(data[i:i + 64], stream))
    return bytes(out)


def poly1305(key: bytes, message: bytes) -> bytes:
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:], "little")
    p, acc = (1 << 130) - 5, 0
    for i in range(0, len(message), 16):
        n = int.from_bytes(message[i:i + 16] + b"\x01", "little")
        acc = (acc + n) * r % p
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(data: bytes) -> bytes:
    return b"\x00" * (-len(data) % 16)


def _tag(key: bytes, nonce: bytes, aad: bytes, ciphertext: bytes) -> bytes:
    otk = _block(key, 0, nonce)[:32]
    mac_data = aad + _pad16(aad) + ciphertext + _pad16(ciphertext) + struct.pack("<QQ", len(aad), len(ciphertext))
    return poly1305(otk, mac_data)


def _fast():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

        return ChaCha20Poly1305
    except ImportError:
        return None


def encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"", pure: bool = False) -> bytes:
    """→ testo cifrato + etichetta di 16 byte."""
    fast = None if pure else _fast()
    if fast is not None:
        return fast(key).encrypt(nonce, plaintext, aad)
    ciphertext = chacha20(key, 1, nonce, plaintext)
    return ciphertext + _tag(key, nonce, aad, ciphertext)


def decrypt(key: bytes, nonce: bytes, data: bytes, aad: bytes = b"", pure: bool = False) -> bytes:
    fast = None if pure else _fast()
    if fast is not None:
        try:
            return fast(key).decrypt(nonce, data, aad)
        except Exception as exc:
            raise DecryptError("dati alterati o chiave sbagliata") from exc
    ciphertext, tag = data[:-16], data[-16:]
    if len(data) < 16 or not hmac.compare_digest(_tag(key, nonce, aad, ciphertext), tag):
        raise DecryptError("dati alterati o chiave sbagliata")
    return chacha20(key, 1, nonce, ciphertext)


def seal(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    nonce = os.urandom(12)
    return nonce + encrypt(key, nonce, plaintext, aad)


def open_sealed(key: bytes, box: bytes, aad: bytes = b"") -> bytes:
    if len(box) < 28:
        raise DecryptError("dati troppo corti")
    return decrypt(key, box[:12], box[12:], aad)
