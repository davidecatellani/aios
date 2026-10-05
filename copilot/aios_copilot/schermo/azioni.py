"""Le azioni di Schermo AIOS per la home e per Nova: chi c'è, apri lo schermo, manda file, anteprime."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .scoperta import Peer, find_peer, load_peers

THUMB_EVERY = 4.0
_thumbs: dict[str, tuple[float, bytes]] = {}
_lock = threading.Lock()


def identity() -> Any:
    from ..identity import Identity

    return Identity.load()


def peers() -> list[Peer]:
    return load_peers()


def _who(query: str) -> tuple[Peer | None, str]:
    found = peers()
    if not found:
        return None, ("Non vedo altri tuoi PC accesi nella rete. Su ogni PC serve la stessa identità AIOS "
                      "(Impostazioni › Account, con la stessa frase di recupero) e devono essere nella stessa rete.")
    peer = find_peer(query, found)
    if peer is None:
        names = ", ".join(p.nome for p in found)
        return None, f"Quale? Vedo: {names}."
    return peer, ""


def open_viewer(query: str, popen: Callable[..., Any] = subprocess.Popen) -> tuple[bool, str]:
    peer, why = _who(query)
    if peer is None:
        return False, why
    exe = shutil.which("aios-schermo") or "aios-schermo"
    try:
        popen([exe, "guarda", peer.id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        return False, f"Non riesco ad aprire lo schermo di {peer.nome}: {exc}"
    return True, f"Apro lo schermo di {peer.nome}: mouse, tastiera, suono e appunti passano di là. Ctrl+Alt+Q per chiudere."


def send(query: str, paths: list[Path], sender: Callable[..., list[str]] | None = None,
         notify: Callable[[str], None] | None = None, wait: bool = False) -> tuple[bool, str]:
    """Manda file a un altro PC (in background, con una notifica alla fine, a meno di `wait`)."""
    from .appunti import describe, human, total_size
    from .servizio import notify as default_notify
    from .servizio import send_files_to

    paths = [p for p in paths if p.exists()]
    if not paths:
        return False, "Non trovo i file da mandare."
    peer, why = _who(query)
    if peer is None:
        return False, why
    me = identity()
    if me is None:
        return False, "Prima serve l'identità AIOS su questo PC (Impostazioni › Account)."
    sender = sender or send_files_to
    notify = notify or default_notify
    what = describe(paths)

    def go() -> str:
        try:
            names = sender(me, peer.indirizzo, peer.porta, paths)
        except Exception as exc:  # rete, rifiuto, spazio
            msg = f"Non sono riuscita a mandare {what} a {peer.nome}: {exc}"
        else:
            msg = f"Mandato {what} a {peer.nome}: è nei suoi Scaricati." if names else f"{peer.nome} non ha accettato {what}."
        if not wait:
            notify(msg)
        return msg

    if wait:
        msg = go()
        return msg.startswith("Mandato"), msg
    threading.Thread(target=go, name="schermo-manda", daemon=True).start()
    return True, f"Mando {what} ({human(total_size(paths))}) a {peer.nome}: ti avviso quando è arrivato."


def thumbnail(peer_id: str, fetch: Callable[..., bytes] | None = None, now: Callable[[], float] = time.monotonic) -> bytes:
    """L'anteprima di un altro PC per la home (al massimo una ogni 4 secondi per PC)."""
    with _lock:
        hit = _thumbs.get(peer_id)
        if hit and now() - hit[0] < THUMB_EVERY:
            return hit[1]
    peer = next((p for p in peers() if p.id == peer_id), None)
    me = identity()
    if peer is None or me is None:
        return b""
    if fetch is None:
        from .servizio import fetch_thumbnail as fetch
    try:
        img = fetch(me, peer.indirizzo, peer.porta, width=480)
    except Exception:
        img = b""
    with _lock:
        _thumbs[peer_id] = (now(), img)
    return img


def choose_files(run: Callable[..., Any] = subprocess.run) -> list[Path]:
    """La finestra per scegliere i file (dalla home, che è una pagina e non vede i percorsi)."""
    try:
        p = run(["zenity", "--file-selection", "--multiple", "--separator=\n", "--title=Manda file all'altro PC"],
                capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return []
    return [Path(x) for x in p.stdout.splitlines() if x.strip()] if p.returncode == 0 else []
