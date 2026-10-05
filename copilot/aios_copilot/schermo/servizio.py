"""Il servizio Schermo AIOS di ogni PC, e i comandi.

    aios-schermo servizio            annuncia questo PC, trova gli altri, accetta i tuoi dispositivi
    aios-schermo elenco              i tuoi dispositivi accesi nella rete
    aios-schermo guarda <nome>       apre lo schermo di un altro PC (nome, parte del nome o indirizzo)
    aios-schermo anteprima <nome> <file.jpg>

Ogni collegamento: stretta di mano (protocollo.py), poi il visore chiede «avvia» (lo schermo dal vivo) o
«anteprima» (una foto piccola, per il pannello della home). Chi viene guardato riceve una notifica.
Le impostazioni in ~/.config/aios/schermo.json: {"condividi": true, "comandi": true} (spegnere
«condividi» chiude lo schermo agli altri; spegnere «comandi» li fa solo guardare).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import PORT
from . import protocollo as P
from .cattura import Capture, Monitor, monitors, pick_monitor, thumbnail

PING_EVERY = 2.0


def settings_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "schermo.json"


def settings() -> dict[str, Any]:
    base = {"condividi": True, "comandi": True}
    try:
        data = json.loads(settings_path().read_text())
        if isinstance(data, dict):
            base.update({k: bool(data[k]) for k in base if k in data})
    except (OSError, ValueError):
        pass
    return base


def notify(text: str) -> None:
    try:
        subprocess.Popen(["notify-send", "-a", "AIOS", "-i", "video-display", "Schermo AIOS", text],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class Shared:
    """Ciò che le sessioni di questo PC hanno in comune: i file offerti negli appunti e chi tiene allineati
    gli appunti (per non rimandare indietro quello che è appena arrivato)."""

    def __init__(self) -> None:
        from .appunti import Offers

        self.offers = Offers()
        self.syncs: list[Any] = []

    def apply_clipboard(self, mime: str, data: bytes, clipboard: Any = None) -> None:
        if self.syncs:
            for sync in self.syncs:
                sync.apply(mime, data)
            return
        from .appunti import Clipboard

        (clipboard or Clipboard()).write(mime, data)


class Session:
    """Un visore collegato a questo PC (o un trasferimento di file, su un collegamento a parte)."""

    def __init__(self, channel: P.Channel, capture: Capture | None = None,
                 list_monitors: Callable[[], list[Monitor]] = monitors,
                 make_injector: Callable[[Monitor | None, list[Monitor]], Any] | None = None,
                 snap: Callable[..., bytes] = thumbnail, conf: dict[str, Any] | None = None,
                 on_notify: Callable[[str], None] = notify, audio: Any = None, clipboard: Any = None,
                 shared: Shared | None = None, folders: Callable[[str], Path] | None = None):
        self.ch = channel
        self.capture = capture or Capture()
        self.list_monitors = list_monitors
        self.make_injector = make_injector
        self.snap = snap
        self.conf = conf or settings()
        self.notify = on_notify
        self.audio = audio
        self.clipboard = clipboard
        self.shared = shared or Shared()
        self.folders = folders or _folder
        self.sync: Any = None
        self.injector: Any = None
        self.monitor: Monitor | None = None
        self.wanted: list[str] = ["h264"]
        self.quality = "alta"
        self._streamer: threading.Thread | None = None
        self._audio_thread: threading.Thread | None = None
        self.streaming = False
        self.watching = False  # ha chiesto lo schermo dal vivo (non solo un'anteprima)

    # --- video e suono ---
    def _start_stream(self, which: str | int | None, with_audio: bool = False) -> None:
        self._stop_stream()
        mons = self.list_monitors()
        self.monitor = pick_monitor(mons, which)
        codec, encoder = self.capture.start(self.wanted, self.monitor, self.quality)
        if not codec:
            self.ch.send_json(P.CONFIG, {"errore": "Su questo PC non riesco a catturare lo schermo (manca wf-recorder o grim)."})
            return
        if self.conf.get("comandi", True) and self.make_injector is not None:
            if self.injector is not None:
                self.injector.close()
            self.injector = self.make_injector(self.monitor, mons)
        sound = False
        if with_audio and self.audio is not None and self._audio_thread is None:
            sound = self.audio.start()
            if sound:
                self._audio_thread = threading.Thread(target=self._pump_audio, name="schermo-audio", daemon=True)
                self._audio_thread.start()
        m = self.monitor
        self.ch.send_json(P.CONFIG, {
            "codec": codec, "codificatore": encoder, "qualita": self.capture.quality,
            "larghezza": m.width if m else 0, "altezza": m.height if m else 0,
            "schermo": m.name if m else "", "schermi": [x.to_dict() for x in mons],
            "comandi": bool(self.injector is not None and self.injector.can_control),
            "audio": sound or self._audio_thread is not None, "appunti": self.sync is not None})
        self.streaming = True
        self._streamer = threading.Thread(target=self._pump, name="schermo-video", daemon=True)
        self._streamer.start()

    def _pump(self) -> None:
        try:
            for chunk in self.capture.chunks():
                self.ch.send(P.VIDEO, chunk)
        except OSError:
            pass
        finally:
            self.streaming = False

    def _pump_audio(self) -> None:
        try:
            for chunk in self.audio.chunks():
                self.ch.send(P.AUDIO, chunk)
        except OSError:
            pass

    def _stop_stream(self) -> None:
        self.capture.stop()
        t, self._streamer = self._streamer, None
        if t is not None:
            t.join(timeout=3)

    def _stop_audio(self) -> None:
        if self.audio is not None:
            self.audio.stop()
        self._audio_thread = None

    # --- appunti ---
    def _start_clipboard(self) -> None:
        if self.sync is not None or self.clipboard is None or not self.conf.get("comandi", True):
            return
        from .appunti import FILES, ClipboardSync, pack_clip

        def send(payload: bytes) -> None:
            self.ch.send(P.CLIPBOARD, payload)

        def offer(paths: list[Path]) -> None:
            info = self.shared.offers.add(paths)
            send(pack_clip(FILES, json.dumps(info).encode()))

        self.sync = ClipboardSync(self.clipboard, send, on_local_files=offer)
        self.shared.syncs.append(self.sync)
        self.sync.start()

    def _stop_clipboard(self) -> None:
        if self.sync is not None:
            self.sync.stop()
            if self.sync in self.shared.syncs:
                self.shared.syncs.remove(self.sync)
            self.sync = None

    # --- file (su un collegamento a parte) ---
    def _give_files(self, token: str) -> None:
        from .appunti import send_files

        paths = self.shared.offers.take(token)
        if paths is None:
            self.ch.send_json(P.FILE, {"tipo": "rifiuto", "motivo": "copia scaduta: ricopia i file"})
            return
        send_files(self.ch, paths)

    def _take_files(self, scope: str) -> None:
        from .appunti import URIS, describe, make_uris, receive_files

        if not self.conf.get("comandi", True):
            self.ch.send_json(P.FILE, {"tipo": "rifiuto", "motivo": "questo PC si può solo guardare"})
            return
        paths = receive_files(self.ch, self.folders(scope))
        if not paths:
            return
        self.ch.send_json(P.FILE, {"tipo": "ricevuti", "file": [p.name for p in paths]})
        if scope == "appunti":
            self.shared.apply_clipboard(URIS, make_uris(paths))
        else:
            self.notify(f"Ricevuto {describe(paths)} da {self.ch.peer.name}: è in Scaricati.")

    # --- messaggi ---
    def handle(self, kind: int, payload: bytes) -> bool:
        """False → chiudere."""
        if kind == P.INPUT:
            if self.injector is not None:
                self.injector.handle(payload)
        elif kind == P.CLIPBOARD:
            if self.sync is not None:
                self.sync.remote(payload)
        elif kind == P.CONTROL:
            msg = P.parse_json(payload)
            what = msg.get("tipo")
            if what == "avvia":
                codecs = [c for c in msg.get("codec", []) if c in ("hevc", "h264", "jpeg")]
                self.wanted = codecs or ["h264", "jpeg"]
                self.quality = str(msg.get("qualita", "alta"))
                if not self.watching:
                    self.watching = True
                    self.notify(f"{self.ch.peer.name} sta guardando questo schermo.")
                if msg.get("appunti", True):
                    self._start_clipboard()
                self._start_stream(msg.get("schermo"), with_audio=bool(msg.get("audio", True)))
            elif what in ("qualita", "schermo", "chiave"):
                self.quality = str(msg.get("qualita", self.quality))
                self._start_stream(msg.get("schermo", self.monitor.name if self.monitor else None))
            elif what == "audio":
                if msg.get("acceso"):
                    self._start_stream(self.monitor.name if self.monitor else None, with_audio=True)
                else:
                    self._stop_audio()
            elif what == "ping":
                self.ch.send_json(P.CONTROL, {"tipo": "pong", "t": msg.get("t")})
            elif what == "anteprima":
                mons = self.list_monitors()
                img = self.snap(pick_monitor(mons, msg.get("schermo")), width=int(msg.get("larghezza", 480)))
                self.ch.send(P.IMAGE, img)
            elif what == "prendi":
                self._give_files(str(msg.get("gettone", "")))
                return False
            elif what == "invia":
                self._take_files("appunti" if msg.get("scopo") == "appunti" else "file")
                return False
        elif kind == P.BYE:
            return False
        return True

    def run(self) -> None:
        try:
            while True:
                kind, payload = self.ch.recv()
                if not self.handle(kind, payload):
                    break
        except (OSError, P.DecryptError, ConnectionError):
            pass
        finally:
            was = self.watching
            self._stop_stream()
            self._stop_audio()
            self._stop_clipboard()
            if self.injector is not None:
                self.injector.close()
            self.ch.close()
            if was:
                self.notify(f"{self.ch.peer.name} ha smesso di guardare questo schermo.")


def _folder(scope: str) -> Path:
    from .appunti import clipboard_dir, downloads

    return clipboard_dir() if scope == "appunti" else downloads()


class Server:
    def __init__(self, identity: Any, port: int = PORT, host: str = "", session: Callable[[P.Channel], Session] | None = None):
        self.identity, self.port, self.host = identity, port, host
        self.session = session or self._default_session
        self._sock: socket.socket | None = None
        self.sessions: list[Session] = []
        self.shared = Shared()

    def _default_session(self, ch: P.Channel) -> Session:
        from .appunti import Clipboard
        from .cattura import AudioCapture
        from .ingresso import Injector

        clip = Clipboard()
        return Session(ch, make_injector=Injector.open, audio=AudioCapture(), clipboard=clip if clip.available() else None,
                       shared=self.shared)

    def bind(self) -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(8)
        self._sock = s
        self.port = s.getsockname()[1]
        return self.port

    def _client(self, conn: socket.socket, addr: Any) -> None:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(10)
        try:
            ch = P.accept(conn, self.identity)
        except (OSError, P.HandshakeError, P.DecryptError, ValueError):
            conn.close()
            return
        conn.settimeout(None)
        if not settings().get("condividi", True):
            ch.close("Questo PC non condivide lo schermo (Impostazioni › Schermo AIOS).")
            return
        sess = self.session(ch)
        self.sessions.append(sess)
        try:
            sess.run()
        finally:
            self.sessions.remove(sess)

    def serve_forever(self) -> None:
        if self._sock is None:
            self.bind()
        while True:
            try:
                conn, addr = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn, addr), name="schermo-collegamento", daemon=True).start()

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()


# --- lato di chi guarda ----------------------------------------------------------------------------
def open_channel(identity: Any, host: str, port: int = PORT, timeout: float = 5) -> P.Channel:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        ch = P.connect(sock, identity)
    except Exception:
        sock.close()
        raise
    sock.settimeout(None)
    return ch


def fetch_thumbnail(identity: Any, host: str, port: int = PORT, width: int = 480, timeout: float = 6) -> bytes:
    ch = open_channel(identity, host, port, timeout)
    try:
        ch.sock.settimeout(timeout)
        ch.send_json(P.CONTROL, {"tipo": "anteprima", "larghezza": width})
        while True:
            kind, payload = ch.recv()
            if kind == P.IMAGE:
                return payload
            if kind == P.BYE:
                return b""
    finally:
        ch.close("fatto")


def send_files_to(identity: Any, host: str, port: int, paths: list[Path], scope: str = "file",
                  progress: Callable[[int, int], None] | None = None) -> list[str]:
    """Manda file (o cartelle) a un altro PC: in Scaricati, o nei suoi appunti. → i nomi arrivati."""
    from .appunti import _expect, send_files

    ch = open_channel(identity, host, port)
    try:
        ch.send_json(P.CONTROL, {"tipo": "invia", "scopo": scope})
        send_files(ch, paths, progress)
        reply = P.parse_json(_expect(ch, P.FILE))
        return list(reply.get("file", []))
    finally:
        ch.close()


def fetch_offer(identity: Any, host: str, port: int, offer: dict[str, Any], dest: Path) -> list[Path]:
    """Scarica i file copiati sull'altro PC (gli appunti), in `dest`."""
    from .appunti import receive_files

    ch = open_channel(identity, host, port)
    try:
        ch.send_json(P.CONTROL, {"tipo": "prendi", "gettone": str(offer.get("gettone", ""))})
        return receive_files(ch, dest)
    finally:
        ch.close()


def _identity() -> Any:
    from ..identity import Identity

    me = Identity.load()
    if me is None:
        print("Questo PC non ha ancora l'identità AIOS: creala (o ripristinala con la frase di recupero) "
              "dalle Impostazioni › Account, sugli altri PC con la stessa frase.", file=sys.stderr)
    return me


def serve(identity: Any) -> int:
    from .scoperta import Discovery, beacon_key

    key = identity.sync_key()
    if not P.fast_cipher():
        print("avviso: manca la libreria cryptography, il video sarà lentissimo", file=sys.stderr)
    server = Server(identity)
    port = server.bind()
    if key:
        cert = identity.certificate
        Discovery(beacon_key(key), {"id": cert.id, "nome": cert.name, "tipo": cert.kind, "porta": port,
                                    "schermi": max(1, len(monitors()))}).start()
    else:
        print("avviso: senza chiave di sincronizzazione gli altri PC non trovano questo da soli", file=sys.stderr)
    server.serve_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aios-schermo", description="Vedere e comandare gli altri tuoi PC nella rete")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("servizio")
    sub.add_parser("elenco")
    g = sub.add_parser("guarda")
    g.add_argument("chi", nargs="?", default="")
    g.add_argument("--qualita", default="alta", choices=["alta", "media", "bassa"])
    i = sub.add_parser("invia")
    i.add_argument("chi")
    i.add_argument("file", nargs="+")
    a = sub.add_parser("anteprima")
    a.add_argument("chi")
    a.add_argument("file")
    args = parser.parse_args(argv)

    from .scoperta import find_peer, load_peers

    if args.cmd == "elenco":
        peers = load_peers()
        if not peers:
            print("Nessun altro tuo dispositivo acceso nella rete (o il servizio aios-schermo è fermo).")
        for p in peers:
            print(f"{p.nome}\t{p.tipo}\t{p.indirizzo}:{p.porta}\t{p.id}")
        return 0
    me = _identity()
    if me is None:
        return 1
    if args.cmd == "servizio":
        return serve(me)

    def target(query: str) -> tuple[str, int, str]:
        peer = find_peer(query)
        if peer is not None:
            return peer.indirizzo, peer.porta, peer.nome
        host, _, port = query.partition(":")
        if host.replace(".", "").isdigit():
            return host, int(port or PORT), host
        raise SystemExit(f"Non trovo «{query}» tra i tuoi dispositivi accesi. Prova: aios-schermo elenco")

    host, port, name = target(args.chi)
    if args.cmd == "anteprima":
        Path(args.file).write_bytes(fetch_thumbnail(me, host, port))
        return 0
    if args.cmd == "invia":
        names = send_files_to(me, host, port, [Path(f).expanduser() for f in args.file])
        print(f"Mandati a {name}: {', '.join(names)}" if names else f"{name} non li ha accettati.")
        return 0 if names else 1
    from .visore import watch

    return watch(me, host, port, name, args.qualita)


if __name__ == "__main__":
    sys.exit(main())
