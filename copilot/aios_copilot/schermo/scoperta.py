"""I dispositivi dell'utente si trovano da soli nella rete di casa.

Ogni PC acceso manda ogni pochi secondi un annuncio UDP (multicast 239.255.43.21, più broadcast per le reti
che il multicast non lo passano). L'annuncio è cifrato con una chiave ricavata dalla chiave di
sincronizzazione, che hanno solo i dispositivi dell'utente: gli altri nella rete vedono byte casuali,
senza nome né modello. Dentro: identificativo del dispositivo, nome, tipo, porta del collegamento e ora.
L'ora evita che un annuncio registrato venga ripetuto più tardi.

La scoperta dice solo «c'è, e sta a quell'indirizzo»: chi è davvero lo prova poi la stretta di mano,
con i certificati (protocollo.py).
"""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from ..crypto import DecryptError, hkdf, open_sealed, seal
from . import DISCOVERY_PORT, PORT

GROUP = "239.255.43.21"
HEADER = b"AIOSv1"
EVERY = 3.0         # un annuncio ogni 3 secondi
GONE_AFTER = 12.0   # chi non si sente da 12 secondi è spento o andato via
MAX_SKEW = 300      # annunci più vecchi di 5 minuti (o dal futuro) si ignorano


def beacon_key(sync_key: bytes) -> bytes:
    return hkdf(sync_key, b"aios-schermo scoperta", 32)


@dataclass
class Peer:
    id: str
    nome: str
    tipo: str
    indirizzo: str
    porta: int
    visto: float
    schermi: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_beacon(key: bytes, me: dict[str, Any], now: float | None = None) -> bytes:
    body = json.dumps({**me, "t": int(now if now is not None else time.time())}, separators=(",", ":")).encode()
    return HEADER + seal(key, body, HEADER)


def read_beacon(key: bytes, data: bytes, now: float | None = None) -> dict[str, Any] | None:
    if not data.startswith(HEADER) or len(data) > 2048:
        return None
    try:
        doc = json.loads(open_sealed(key, data[len(HEADER):], HEADER))
    except (DecryptError, ValueError):
        return None  # non è dei nostri
    now = now if now is not None else time.time()
    if not isinstance(doc, dict) or abs(now - float(doc.get("t", 0))) > MAX_SKEW or not doc.get("id"):
        return None
    return doc


def peers_path() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "aios-schermo" / "vicini.json"


def load_peers(now: float | None = None) -> list[Peer]:
    """I dispositivi visti dal servizio (per la shell e Nova, che non ascoltano la rete da soli)."""
    now = now if now is not None else time.time()
    try:
        rows = json.loads(peers_path().read_text())
    except (OSError, ValueError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            p = Peer(**{k: r[k] for k in ("id", "nome", "tipo", "indirizzo", "porta", "visto")}, schermi=int(r.get("schermi", 1)))
        except (KeyError, TypeError):
            continue
        if now - p.visto <= GONE_AFTER:
            out.append(p)
    return sorted(out, key=lambda p: p.nome.lower())


def find_peer(query: str, peers: list[Peer] | None = None) -> Peer | None:
    """Per nome («il pc da gaming», «gaming»), identificativo o indirizzo."""
    peers = load_peers() if peers is None else peers
    q = query.lower().strip()
    for p in peers:
        if q in (p.id, p.indirizzo, p.nome.lower()):
            return p
    words = [w for w in q.replace("-", " ").split() if len(w) > 2 and w not in ("del", "dal", "della", "computer", "il", "pc")]
    hits = [p for p in peers if any(w in p.nome.lower() for w in words)]
    if len(hits) == 1:
        return hits[0]
    return peers[0] if len(peers) == 1 and q in ("", "altro", "l'altro", "altro pc", "l'altro pc", "l'altro computer") else None


class Discovery:
    """Annuncia questo dispositivo e tiene l'elenco degli altri dell'utente."""

    def __init__(self, key: bytes, me: dict[str, Any], port: int = DISCOVERY_PORT,
                 on_change: Callable[[list[Peer]], None] | None = None, save: bool = True):
        self.key, self.me, self.port = key, dict(me), port
        self.peers: dict[str, Peer] = {}
        self.on_change = on_change
        self.save = save
        self._stop = threading.Event()
        self._sock: socket.socket | None = None

    def _socket(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("", self.port))
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton("0.0.0.0")))
        except OSError:
            pass  # senza rete (ancora): resta il broadcast
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)  # solo la rete di casa, mai oltre il router
        s.settimeout(1.0)
        return s

    def announce(self) -> None:
        if self._sock is None:
            return
        data = make_beacon(self.key, self.me)
        for target in ((GROUP, self.port), ("255.255.255.255", self.port)):
            try:
                self._sock.sendto(data, target)
            except OSError:
                pass

    def handle(self, data: bytes, addr: tuple[str, int], now: float | None = None) -> Peer | None:
        doc = read_beacon(self.key, data, now)
        if doc is None or doc["id"] == self.me.get("id"):
            return None
        now = now if now is not None else time.time()
        known = doc["id"] in self.peers
        peer = Peer(str(doc["id"]), str(doc.get("nome", "dispositivo"))[:60], str(doc.get("tipo", "pc")), addr[0],
                    int(doc.get("porta", PORT)), now, int(doc.get("schermi", 1)))
        self.peers[peer.id] = peer
        if not known:
            self._changed()
            self.announce()  # risponde subito: chi è appena arrivato ci vede senza aspettare il giro
        return peer

    def expire(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        gone = [k for k, p in self.peers.items() if now - p.visto > GONE_AFTER]
        for k in gone:
            del self.peers[k]
        if gone:
            self._changed()

    def _changed(self) -> None:
        if self.on_change:
            self.on_change(list(self.peers.values()))
        self.write()

    def write(self) -> None:
        if not self.save:
            return
        path = peers_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps([p.to_dict() for p in self.peers.values()], ensure_ascii=False))
            tmp.replace(path)
        except OSError:
            pass

    def run(self) -> None:
        last = 0.0
        while not self._stop.is_set():
            if self._sock is None:
                try:
                    self._sock = self._socket()
                except OSError:
                    self._stop.wait(5)
                    continue
            now = time.time()
            if now - last >= EVERY:
                self.announce()
                self.expire(now)
                self.write()  # aggiorna «visto» per chi legge il file
                last = now
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                self._sock.close()
                self._sock = None
                continue
            self.handle(data, addr)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="schermo-scoperta", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
