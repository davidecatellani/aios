"""Il protocollo Schermo AIOS, versione 1.

Stretta di mano (tre messaggi, sul modello di Noise XX / SIGMA):

    1. C → S   "AIOS-SCHERMO/1\\n" · eC (X25519 effimera, 32) · nC (16 casuali)
    2. S → C   eS (32) · nS (16) · [lunghezza u32 · AEAD_hs(certificato di S + firma di S su h1)]
    3. C → S   [lunghezza u32 · AEAD_hs(certificato di C + firma di C su h2)]

    h1 = SHA-256(msg1 · eS · nS), h2 = SHA-256(h1 · busta 2), h3 = SHA-256(h2 · busta 3)
    chiave della stretta = HKDF(X25519(e, e'), sale h1)
    chiavi di sessione   = HKDF(X25519(e, e'), sale h3, "c2s" / "s2c")

Ognuno firma con la chiave del suo dispositivo, e il certificato deve essere firmato dalla chiave
dell'utente (identity.py) e non revocato: si collegano solo i dispositivi della stessa persona. Le
chiavi effimere danno la segretezza in avanti (chi ruba una chiave domani non legge le sessioni di ieri);
i certificati viaggiano già cifrati (chi ascolta la rete non sa nemmeno quali dispositivi si parlano).

Dopo, ogni messaggio è un riquadro:  lunghezza u32 · AES-256-GCM(tipo u8 · dati), con il nonce dato da un
contatore per direzione (niente ripetizioni, niente riordini: TCP). Se il bit alto del tipo è acceso, i
dati sono compressi con zlib (comandi e appunti; il video è già compresso).
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import threading
import zlib
from typing import Any

from .. import ed25519
from ..crypto import DecryptError, decrypt, encrypt, hkdf, x25519, x25519_public
from ..identity import Certificate, IdentityError, b64, canonical, unb64

MAGIC = b"AIOS-SCHERMO/1\n"
MAX_FRAME = 16 * 1024 * 1024
MAX_HANDSHAKE = 16 * 1024
COMPRESSED = 0x80

# tipi di messaggio
CONFIG = 1     # S → C  json: codec, dimensioni, schermo
VIDEO = 2      # S → C  un pezzo del flusso video (H.264/H.265 Annex B), o un'immagine JPEG intera
INPUT = 3      # C → S  eventi di mouse e tastiera (formato binario sotto)
CONTROL = 4    # ↔      json: avvia, qualità, ping/pong, fotogramma chiave, anteprima
IMAGE = 5      # S → C  un'anteprima JPEG
CLIPBOARD = 6  # ↔      testo degli appunti
BYE = 7        # ↔      chiusura, con il motivo
AUDIO = 8      # S → C  un pezzo del flusso audio (Opus in Ogg)
FILE = 9       # ↔      json: inizio, fine o rifiuto di un file (su un collegamento a parte, appunti.py)
FILE_DATA = 10  # ↔     un pezzo di file
NAMES = {CONFIG: "config", VIDEO: "video", INPUT: "input", CONTROL: "control", IMAGE: "image", CLIPBOARD: "clipboard",
         BYE: "bye", AUDIO: "audio", FILE: "file", FILE_DATA: "file_data"}

# eventi di INPUT, uno dopo l'altro nello stesso messaggio
EV_MOVE = 1    # x u16, y u16: posizione normalizzata 0..65535 sullo schermo trasmesso
EV_BUTTON = 2  # tasto u8 (1 sinistro, 2 centrale, 3 destro), premuto u8
EV_WHEEL = 3   # dx i16, dy i16 in 1/120 di scatto (come le rotelline ad alta risoluzione)
EV_KEY = 4     # codice evdev u16, premuto u8
EV_RELEASE_ALL = 5  # rilascia tutto (la finestra ha perso il fuoco)
_EV = {EV_MOVE: ">HH", EV_BUTTON: ">BB", EV_WHEEL: ">hh", EV_KEY: ">HB", EV_RELEASE_ALL: ""}


class HandshakeError(ConnectionError):
    pass


# --- cifratura del canale -----------------------------------------------------------------------------
class _Aead:
    """AES-256-GCM dalla libreria `cryptography` (con le istruzioni AES del processore: gigabyte al
    secondo); senza, ChaCha20-Poly1305 in Python puro (lento: va per le prove e le anteprime, non per il
    video)."""

    def __init__(self, key: bytes):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            self._fast = AESGCM(key)
        except ImportError:  # pragma: no cover - dipende dal sistema
            self._fast = None
        self.key = key

    def seal(self, counter: int, data: bytes) -> bytes:
        nonce = struct.pack(">IQ", 0, counter)
        if self._fast is not None:
            return self._fast.encrypt(nonce, data, None)
        return encrypt(self.key, nonce, data)

    def open(self, counter: int, data: bytes) -> bytes:
        nonce = struct.pack(">IQ", 0, counter)
        if self._fast is not None:
            try:
                return self._fast.decrypt(nonce, data, None)
            except Exception as exc:
                raise DecryptError("riquadro alterato") from exc
        return decrypt(self.key, nonce, data)


def fast_cipher() -> bool:
    try:
        import cryptography.hazmat.primitives.ciphers.aead  # noqa: F401

        return True
    except ImportError:  # pragma: no cover
        return False


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("collegamento chiuso")
        buf += chunk
    return bytes(buf)


class Channel:
    """Il canale cifrato dopo la stretta di mano. `send` si può chiamare da più thread."""

    def __init__(self, sock: socket.socket, send_key: bytes, recv_key: bytes, peer: Certificate):
        self.sock = sock
        self.peer = peer
        self._out, self._in = _Aead(send_key), _Aead(recv_key)
        self._out_n = self._in_n = 0
        self._lock = threading.Lock()
        self.closed = False
        self.sent_bytes = self.recv_bytes = 0

    def send(self, kind: int, payload: bytes = b"") -> None:
        if kind in (CONTROL, CLIPBOARD, CONFIG, BYE, FILE) and len(payload) > 256:
            packed = zlib.compress(payload, 6)
            if len(packed) < len(payload):
                kind, payload = kind | COMPRESSED, packed
        body = bytes([kind]) + payload
        with self._lock:
            if self.closed:
                raise ConnectionError("collegamento chiuso")
            frame = self._out.seal(self._out_n, body)
            self._out_n += 1
            self.sock.sendall(struct.pack(">I", len(frame)) + frame)
            self.sent_bytes += len(frame) + 4

    def send_json(self, kind: int, obj: dict[str, Any]) -> None:
        self.send(kind, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode())

    def recv(self) -> tuple[int, bytes]:
        (size,) = struct.unpack(">I", _recv_exact(self.sock, 4))
        if not 17 <= size <= MAX_FRAME:
            raise ConnectionError("riquadro di dimensione non valida")
        body = self._in.open(self._in_n, _recv_exact(self.sock, size))
        self._in_n += 1
        self.recv_bytes += size + 4
        kind, payload = body[0], body[1:]
        if kind & COMPRESSED:
            kind &= ~COMPRESSED
            d = zlib.decompressobj()
            payload = d.decompress(payload, MAX_FRAME)
            if d.unconsumed_tail:
                raise ConnectionError("dati compressi troppo grandi")
        return kind, payload

    def close(self, reason: str = "") -> None:
        if self.closed:
            return
        try:
            if reason:
                self.send_json(BYE, {"motivo": reason})
        except OSError:
            pass
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def parse_json(payload: bytes) -> dict[str, Any]:
    try:
        obj = json.loads(payload.decode())
    except (UnicodeDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


# --- stretta di mano ----------------------------------------------------------------------------------
def _h(*parts: bytes) -> bytes:
    return hashlib.sha256(b"".join(parts)).digest()


def _dh(private: bytes, public: bytes) -> bytes:
    shared = x25519(private, public)
    if shared == bytes(32):
        raise HandshakeError("chiave effimera non valida")
    return shared


def _proof(identity: Any, label: bytes, h: bytes) -> bytes:
    sig = ed25519.sign(identity.device_seed(), label + h)
    return canonical({"cert": identity.certificate.to_dict(), "sig": b64(sig)})


def _check_proof(identity: Any, data: bytes, label: bytes, h: bytes) -> Certificate:
    try:
        doc = json.loads(data)
        cert = Certificate.from_dict(doc["cert"])
        sig = unb64(doc["sig"])
        public = unb64(cert.public)
    except (ValueError, KeyError, TypeError, IdentityError) as exc:
        raise HandshakeError("prova d'identità illeggibile") from exc
    if not identity.trusts(cert):
        raise HandshakeError("dispositivo non tuo (o revocato)")
    if not ed25519.verify(public, label + h, sig):
        raise HandshakeError("firma del dispositivo non valida")
    return cert


def _send_box(sock: socket.socket, box: bytes) -> None:
    sock.sendall(struct.pack(">I", len(box)) + box)


def _recv_box(sock: socket.socket) -> bytes:
    (size,) = struct.unpack(">I", _recv_exact(sock, 4))
    if not 17 <= size <= MAX_HANDSHAKE:
        raise HandshakeError("stretta di mano non valida")
    return _recv_exact(sock, size)


def _session(shared: bytes, h3: bytes) -> tuple[bytes, bytes]:
    return hkdf(shared, b"aios-schermo c2s", 32, salt=h3), hkdf(shared, b"aios-schermo s2c", 32, salt=h3)


def connect(sock: socket.socket, identity: Any) -> Channel:
    """Lato di chi si collega (il visore)."""
    e = os.urandom(32)
    msg1 = MAGIC + x25519_public(e) + os.urandom(16)
    sock.sendall(msg1)
    head = _recv_exact(sock, 48)
    e_s = head[:32]
    shared = _dh(e, e_s)
    h1 = _h(msg1, head)
    hs = _Aead(hkdf(shared, b"aios-schermo hs", 32, salt=h1))
    box2 = _recv_box(sock)
    try:
        server = _check_proof(identity, hs.open(1, box2), b"server", h1)
    except DecryptError as exc:
        raise HandshakeError("stretta di mano alterata") from exc
    h2 = _h(h1, box2)
    box3 = hs.seal(2, _proof(identity, b"client", h2))
    _send_box(sock, box3)
    c2s, s2c = _session(shared, _h(h2, box3))
    return Channel(sock, c2s, s2c, server)


def accept(sock: socket.socket, identity: Any) -> Channel:
    """Lato di chi viene guardato (il servizio)."""
    msg1 = _recv_exact(sock, len(MAGIC) + 48)
    if not msg1.startswith(MAGIC):
        raise HandshakeError("non è un visore AIOS")
    e = os.urandom(32)
    head = x25519_public(e) + os.urandom(16)
    shared = _dh(e, msg1[len(MAGIC):len(MAGIC) + 32])
    h1 = _h(msg1, head)
    hs = _Aead(hkdf(shared, b"aios-schermo hs", 32, salt=h1))
    box2 = hs.seal(1, _proof(identity, b"server", h1))
    sock.sendall(head)
    _send_box(sock, box2)
    h2 = _h(h1, box2)
    box3 = _recv_box(sock)
    try:
        client = _check_proof(identity, hs.open(2, box3), b"client", h2)
    except DecryptError as exc:
        raise HandshakeError("stretta di mano alterata") from exc
    c2s, s2c = _session(shared, _h(h2, box3))
    return Channel(sock, s2c, c2s, client)


# --- eventi di mouse e tastiera ---------------------------------------------------------------------
def pack_events(events: list[tuple]) -> bytes:
    out = bytearray()
    for ev in events:
        kind, args = ev[0], ev[1:]
        if kind not in _EV:
            continue
        out.append(kind)
        if _EV[kind]:
            out += struct.pack(_EV[kind], *args)
    return bytes(out)


def unpack_events(data: bytes) -> list[tuple]:
    out, i = [], 0
    while i < len(data):
        kind = data[i]
        i += 1
        fmt = _EV.get(kind)
        if fmt is None:
            break  # evento sconosciuto (versione più nuova): il resto non si può leggere
        size = struct.calcsize(fmt) if fmt else 0
        if i + size > len(data):
            break
        out.append((kind, *struct.unpack(fmt, data[i:i + size])) if fmt else (kind,))
        i += size
    return out
