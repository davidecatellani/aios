"""Firme Ed25519 (RFC 8032), senza dipendenze esterne.

Implementazione di riferimento della RFC 8032 §6: verifica (catalogo dei modelli,
market dei temi) e firma (identità dell'utente e dei suoi dispositivi, identity.py).
È lenta (decine di millisecondi) ma si usa poche volte. Non è a tempo costante: va
bene per chiavi che non firmano messaggi scelti da altri in quantità.
"""

from __future__ import annotations

import hashlib

P = 2**255 - 19
Q = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)

Point = tuple[int, int, int, int]  # coordinate estese (X, Y, Z, T)


def _add(a: Point, b: Point) -> Point:
    A = (a[1] - a[0]) * (b[1] - b[0]) % P
    B = (a[1] + a[0]) * (b[1] + b[0]) % P
    C = 2 * a[3] * b[3] * D % P
    Dd = 2 * a[2] * b[2] % P
    E, F, G, H = B - A, Dd - C, Dd + C, B + A
    return (E * F % P, G * H % P, F * G % P, E * H % P)


def _mul(s: int, point: Point) -> Point:
    result: Point = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            result = _add(result, point)
        point = _add(point, point)
        s >>= 1
    return result


def _equal(a: Point, b: Point) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


def _decompress(data: bytes) -> Point | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


_GY = 4 * pow(5, P - 2, P) % P
_GX = _recover_x(_GY, 0)
BASE: Point = (_GX, _GY, 1, _GX * _GY % P)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decompress(public_key)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % Q
    return _equal(_mul(s, BASE), _add(r, _mul(h, a)))


def _compress(point: Point) -> bytes:
    zinv = pow(point[2], P - 2, P)
    x, y = point[0] * zinv % P, point[1] * zinv % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _expand(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("il seme Ed25519 deve essere di 32 byte")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    a, _ = _expand(seed)
    return _compress(_mul(a, BASE))


def sign(seed: bytes, message: bytes) -> bytes:
    a, prefix = _expand(seed)
    pub = _compress(_mul(a, BASE))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % Q
    big_r = _compress(_mul(r, BASE))
    h = int.from_bytes(hashlib.sha512(big_r + pub + message).digest(), "little") % Q
    return big_r + ((r + h * a) % Q).to_bytes(32, "little")


def to_x25519_public(public: bytes) -> bytes:
    """La stessa chiave vista come chiave di cifratura (u = (1 + y) / (1 - y)), come in libsodium."""
    y = int.from_bytes(public, "little") & ((1 << 255) - 1)
    return ((1 + y) * pow(1 - y, P - 2, P) % P).to_bytes(32, "little")


def to_x25519_private(seed: bytes) -> bytes:
    """Lo scalare privato di Ed25519 (già «clampato»), usabile con X25519."""
    a, _ = _expand(seed)
    return a.to_bytes(32, "little")
