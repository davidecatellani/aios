"""Copia e incolla tra i PC, e i file da un PC all'altro.

Appunti: mentre un visore è collegato, quello che copi su un PC si può incollare sull'altro, in tutte e due
le direzioni: testo, immagini (PNG) e file. Gli appunti di Wayland si leggono e si scrivono con wl-clipboard
(wl-paste --watch avvisa a ogni copia, senza controllare di continuo).

File: copiare un file (o una cartella) nel gestore dei file e incollarlo sull'altro PC funziona come sullo
stesso PC. Si manda prima solo l'elenco (nomi e dimensioni); i file viaggiano su un collegamento a parte,
così il video non si ferma, e finiscono in ~/.cache/aios/appunti, da dove l'incolla li copia dove vuoi.
Per mandare file e basta: trascinarli nella finestra del visore, o «aios-schermo invia <pc> <file>», o
chiederlo a Nova; arrivano in Scaricati, con una notifica.

Un PC dà i suoi file solo a chi li ha ricevuti negli appunti (un gettone casuale per ogni copia, valido
10 minuti), e solo a un dispositivo dell'utente (la stretta di mano lo garantisce).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from . import protocollo as P

TEXT = "text/plain;charset=utf-8"
PNG = "image/png"
URIS = "text/uri-list"
FILES = "application/x-aios-file"  # l'elenco dei file offerti (json), al posto dei file stessi
MAX_CLIP = 32 * 1024 * 1024
CHUNK = 256 * 1024
OFFER_TTL = 600
SPARE = 512 * 1024 * 1024  # spazio libero da lasciare sul disco
CLIP_FILES_MAX = 2 * 1024**3  # oltre, copia e incolla non sposta i file da solo: si trascinano nel visore


def pack_clip(mime: str, data: bytes) -> bytes:
    m = mime.encode()[:255]
    return bytes([len(m)]) + m + data


def unpack_clip(payload: bytes) -> tuple[str, bytes]:
    if not payload:
        return "", b""
    n = payload[0]
    return payload[1:1 + n].decode(errors="replace"), payload[1 + n:]


def parse_uris(data: bytes) -> list[Path]:
    out = []
    for line in data.decode(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("file://"):
            path = Path(urllib.parse.unquote(urllib.parse.urlparse(line).path))
            if path.exists():
                out.append(path)
    return out


def make_uris(paths: list[Path]) -> bytes:
    return "".join(p.resolve().as_uri() + "\r\n" for p in paths).encode()


# --- gli appunti di questo PC ---------------------------------------------------------------------
class Clipboard:
    def __init__(self, runner: Callable[..., Any] = subprocess.run, popen: Callable[..., Any] = subprocess.Popen):
        self.runner, self.popen = runner, popen
        self._watch: Any = None

    def available(self) -> bool:
        return bool(shutil.which("wl-paste") and shutil.which("wl-copy"))

    def read(self) -> tuple[str, bytes] | None:
        try:
            types = self.runner(["wl-paste", "--list-types"], capture_output=True, timeout=3).stdout.decode().split()
        except (OSError, subprocess.SubprocessError):
            return None
        for mime in (URIS, PNG, TEXT, "text/plain", "UTF8_STRING"):
            if mime in types:
                try:
                    p = self.runner(["wl-paste", "--no-newline", "--type", mime], capture_output=True, timeout=10)
                except (OSError, subprocess.SubprocessError):
                    return None
                if p.returncode == 0 and 0 < len(p.stdout) <= MAX_CLIP:
                    return (TEXT if mime in ("text/plain", "UTF8_STRING") else mime), p.stdout
                return None
        return None

    def write(self, mime: str, data: bytes) -> None:
        try:
            self.runner(["wl-copy", "--type", mime], input=data, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

    def watch(self, on_change: Callable[[], None]) -> None:
        """Chiama `on_change` a ogni nuova copia (finché non si chiama stop)."""
        try:
            self._watch = self.popen(["wl-paste", "--watch", "echo", "."], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        except OSError:
            return

        def loop(proc: Any) -> None:
            for _line in proc.stdout:
                on_change()

        threading.Thread(target=loop, args=(self._watch,), name="appunti", daemon=True).start()

    def stop(self) -> None:
        proc, self._watch = self._watch, None
        if proc is not None and proc.poll() is None:
            proc.terminate()


class ClipboardSync:
    """Tiene allineati gli appunti di qui e quelli dell'altro PC.

    `send(payload)` manda un messaggio CLIPBOARD; `on_local_files(paths)` decide cosa fare dei file copiati
    qui (offrirli, o mandarli subito); `fetch_files(offer)` scarica quelli offerti dall'altro → percorsi."""

    def __init__(self, clipboard: Any, send: Callable[[bytes], None],
                 on_local_files: Callable[[list[Path]], None] | None = None,
                 fetch_files: Callable[[dict[str, Any]], list[Path]] | None = None):
        self.clipboard, self.send = clipboard, send
        self.on_local_files, self.fetch_files = on_local_files, fetch_files
        self.last = b""
        self._lock = threading.Lock()

    @staticmethod
    def _hash(mime: str, data: bytes) -> bytes:
        return hashlib.sha256(mime.encode() + b"\0" + data).digest()

    def start(self) -> None:
        self.clipboard.watch(self.local_changed)

    def stop(self) -> None:
        self.clipboard.stop()

    def local_changed(self) -> None:
        got = self.clipboard.read()
        if got is None:
            return
        mime, data = got
        with self._lock:
            h = self._hash(mime, data)
            if h == self.last:
                return  # è quello appena arrivato dall'altro PC
            self.last = h
        if mime == URIS:
            paths = parse_uris(data)
            if paths and self.on_local_files is not None:
                self.on_local_files(paths)
                return
        try:
            self.send(pack_clip(mime, data))
        except OSError:
            pass

    def remote(self, payload: bytes) -> None:
        mime, data = unpack_clip(payload)
        if mime == FILES:
            if self.fetch_files is None:
                return
            try:
                offer = json.loads(data)
            except ValueError:
                return

            def fetch() -> None:
                paths = self.fetch_files(offer)
                if paths:
                    self.apply(URIS, make_uris(paths))

            threading.Thread(target=fetch, name="appunti-file", daemon=True).start()
            return
        if mime in (TEXT, PNG, URIS) and data:
            self.apply(mime, data)

    def apply(self, mime: str, data: bytes) -> None:
        with self._lock:
            self.last = self._hash(mime, data)
        self.clipboard.write(mime, data)


# --- file ------------------------------------------------------------------------------------------
class Offers:
    """I file copiati qui e offerti a un altro PC: gettone → percorsi."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._items: dict[str, tuple[float, list[Path]]] = {}

    def add(self, paths: list[Path]) -> dict[str, Any]:
        now = self.clock()
        self._items = {k: v for k, v in self._items.items() if now - v[0] < OFFER_TTL}
        token = secrets.token_urlsafe(16)
        self._items[token] = (now, paths)
        files = list(walk(paths))
        return {"gettone": token, "file": [n for n, _ in files][:50], "numero": len(files),
                "dimensione": sum(p.stat().st_size for _, p in files)}

    def take(self, token: str) -> list[Path] | None:
        item = self._items.get(token)
        if item is None or self.clock() - item[0] >= OFFER_TTL:
            return None
        return item[1]


def walk(paths: list[Path]) -> list[tuple[str, Path]]:
    """(nome relativo, percorso) dei file, anche dentro le cartelle (senza seguire i collegamenti)."""
    out = []
    for root in paths:
        if root.is_file():
            out.append((root.name, root))
        elif root.is_dir():
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if not Path(dirpath, d).is_symlink())
                for f in sorted(filenames):
                    p = Path(dirpath, f)
                    if p.is_file() and not p.is_symlink():
                        out.append((str(Path(root.name) / p.relative_to(root)), p))
    return out


def safe_name(name: str) -> Path | None:
    """Un nome relativo sicuro: niente percorsi assoluti, «..» o caratteri di controllo."""
    parts = [p for p in re.split(r"[\\/]+", name) if p not in ("", ".")]
    if not parts or any(p == ".." or any(ord(c) < 32 for c in p) for p in parts):
        return None
    return Path(*[p[:200] for p in parts])


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 10000):
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}-{secrets.token_hex(4)}{suffix}")


def send_files(ch: P.Channel, paths: list[Path], progress: Callable[[int, int], None] | None = None) -> int:
    files = walk(paths)
    total = sum(p.stat().st_size for _, p in files)
    ch.send_json(P.FILE, {"tipo": "elenco", "numero": len(files), "dimensione": total})
    reply = P.parse_json(_expect(ch, P.FILE))
    if reply.get("tipo") == "rifiuto":
        raise OSError(reply.get("motivo", "rifiutato"))
    done = 0
    for name, path in files:
        h = hashlib.sha256()
        ch.send_json(P.FILE, {"tipo": "inizio", "nome": name, "dimensione": path.stat().st_size})
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK):
                h.update(chunk)
                ch.send(P.FILE_DATA, chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        ch.send_json(P.FILE, {"tipo": "fine", "sha256": h.hexdigest()})
    ch.send_json(P.FILE, {"tipo": "finito"})
    return len(files)


def _expect(ch: P.Channel, kind: int) -> bytes:
    while True:
        k, payload = ch.recv()
        if k == kind:
            return payload
        if k == P.BYE:
            raise ConnectionError(P.parse_json(payload).get("motivo", "chiuso"))


def receive_files(ch: P.Channel, dest: Path, free: Callable[[Path], int] | None = None) -> list[Path]:
    """Riceve i file in `dest` (le cartelle si ricreano dentro). → i percorsi di primo livello arrivati."""
    free = free or (lambda d: shutil.disk_usage(d).free)
    dest.mkdir(parents=True, exist_ok=True)
    head = P.parse_json(_expect(ch, P.FILE))
    if head.get("tipo") != "elenco":
        return []  # rifiutato dall'altro PC (copia scaduta, solo visione)
    size = int(head.get("dimensione", 0))
    if size + SPARE > free(dest):
        ch.send_json(P.FILE, {"tipo": "rifiuto", "motivo": "spazio insufficiente su questo PC"})
        return []
    ch.send_json(P.FILE, {"tipo": "pronto"})
    tops: list[Path] = []
    renamed: dict[str, Path] = {}  # primo livello già presente → nome nuovo, per tutta la cartella
    current = None
    while True:
        kind, payload = ch.recv()
        if kind == P.FILE_DATA and current is not None:
            current[1].write(payload)
            current[2].update(payload)
            continue
        if kind == P.BYE:
            break
        if kind != P.FILE:
            continue
        msg = P.parse_json(payload)
        what = msg.get("tipo")
        if what == "inizio":
            rel = safe_name(str(msg.get("nome", "")))
            if rel is None:
                raise ConnectionError("nome di file non valido")
            top = rel.parts[0]
            if top not in renamed:
                renamed[top] = unique(dest / top)
                tops.append(renamed[top])
            target = renamed[top].joinpath(*rel.parts[1:]) if len(rel.parts) > 1 else renamed[top]
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".parziale")
            current = (target, open(part, "wb"), hashlib.sha256(), part)
        elif what == "fine" and current is not None:
            target, f, h, part = current
            f.close()
            current = None
            if h.hexdigest() != msg.get("sha256"):
                part.unlink(missing_ok=True)
                raise ConnectionError(f"«{target.name}» è arrivato rovinato")
            part.replace(target)
        elif what == "finito":
            break
    if current is not None:
        current[1].close()
        current[3].unlink(missing_ok=True)
    return tops


def downloads() -> Path:
    from ..xdg import resolve_folder

    return resolve_folder("DOWNLOAD")


def clipboard_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "aios" / "appunti" / time.strftime("%Y%m%d-%H%M%S")


def total_size(paths: list[Path]) -> int:
    return sum(p.stat().st_size for _, p in walk(paths))


def human(size: int) -> str:
    for unit in ("byte", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("byte", "KB") else f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024
    return str(size)


def describe(paths: list[Path]) -> str:
    if len(paths) == 1:
        return f"«{paths[0].name}»"
    return f"{len(paths)} elementi"
