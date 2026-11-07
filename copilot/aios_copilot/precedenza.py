"""Precedenza a quello che l'utente aspetta: mentre si parla con Nova i lavori in sottofondo si fanno da parte.

Sullo stesso PC lavorano più cose che usano l'AI: Nova che risponde, le descrizioni delle foto, il significato dei
documenti, il programmatore delle personalizzazioni. Il pianificatore dei lavori (learning.py) li fa partire
quando il PC è libero, ma da solo non sa quando si parla con Nova (a voce non si toccano mouse e tastiera).
- Ogni richiesta a Nova apre un «turno dell'utente» (user_turn): un segno in XDG_RUNTIME_DIR che vedono tutti i
  processi di SoIA. Finché c'è (e per qualche secondo dopo, se la conversazione continua) il sottofondo aspetta.
- Una richiesta di sottofondo già partita verso llama-server (il nucleo) si interrompe (background_chat) e poi
  riprende: llama-server tiene in memoria la parte già letta (l'immagine, il testo), quindi la ripresa non
  rifà il lavoro lento; un testo libero riparte dal punto in cui era arrivato.
"""

from __future__ import annotations

import http.client
import itertools
import json
import os
import socket
import tempfile
import threading
import time
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

GRACE = 20.0  # secondi dopo una risposta di Nova in cui il sottofondo aspetta ancora (spesso si continua a parlare)
STALE = 600.0  # un segno più vecchio è di un processo morto
_counter = itertools.count()


def marks_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    d = Path(base) / "aios-precedenza"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _own_prefix() -> str:
    return f"{os.getpid()}-{threading.get_ident()}-"


@contextmanager
def user_turn() -> Iterator[None]:
    """Durante una richiesta dell'utente a Nova (o mentre parla): il sottofondo cede il passo."""
    mark = marks_dir() / f"{_own_prefix()}{next(_counter)}"
    try:
        mark.write_text(str(time.time()))
    except OSError:
        mark = None  # type: ignore[assignment]
    try:
        yield
    finally:
        if mark is not None:
            try:
                mark.unlink(missing_ok=True)
                (marks_dir() / "ultima").write_text(str(time.time()))
            except OSError:
                pass


def busy(grace: float = GRACE, ignore_own: bool = False, now: float | None = None) -> bool:
    """C'è (o c'è appena stato) un turno dell'utente? Con ignore_own non conta quello del thread che chiede
    (il programmatore lanciato da Nova lavora dentro il turno che lo ha chiesto)."""
    now = time.time() if now is None else now
    d = marks_dir()
    own = _own_prefix()
    try:
        entries = list(d.iterdir())
    except OSError:
        return False
    for f in entries:
        try:
            age = now - f.stat().st_mtime
        except OSError:
            continue
        if f.name == "ultima":
            if age < grace:
                return True
            continue
        if age > STALE:
            f.unlink(missing_ok=True)
            continue
        if ignore_own and f.name.startswith(own):
            continue
        return True
    return False


def wait_turn(limit: float | None = None, poll: float = 0.5, ignore_own: bool = True,
              sleep: Callable[[float], None] = time.sleep) -> bool:
    """Aspetta che l'utente abbia finito con Nova → True; False se passa `limit` secondi."""
    start = time.monotonic()
    while busy(ignore_own=ignore_own):
        if limit is not None and time.monotonic() - start > limit:
            return False
        sleep(poll)
    return True


def background_chat(url: str, payload: dict[str, Any], timeout: float = 900.0,
                    should_stop: Callable[[], bool] = busy, resume: bool = True,
                    wait: Callable[[], bool] = wait_turn) -> str:
    """Una richiesta di sottofondo a llama-server (/v1/chat/completions) che cede il passo all'utente.

    Se durante il calcolo arriva l'utente, la connessione si chiude (llama-server smette subito di calcolare);
    finito il turno dell'utente si riprende: con `resume` il pezzo già scritto torna come inizio della risposta
    (prefill), senza `resume` (risposte con uno schema JSON) si rifà la risposta, ma la parte lenta (immagine e
    testo già letti) llama-server la ritrova nella sua memoria."""
    target = urllib.parse.urlparse(url)
    messages = list(payload.get("messages", []))
    done = ""
    for _ in range(20):
        wait()
        body = dict(payload, stream=True,
                    messages=messages + ([{"role": "assistant", "content": done}] if done and resume else []))
        text, interrupted = _stream(target, body, timeout, should_stop)
        if resume and done and not text.startswith(done):
            text = done + text  # le versioni che non ripetono il pezzo già scritto
        if not interrupted:
            return text
        done = text if resume else ""
    return done


def _stream(target: urllib.parse.ParseResult, body: dict[str, Any], timeout: float,
            should_stop: Callable[[], bool]) -> tuple[str, bool]:
    conn = http.client.HTTPConnection(target.hostname or "127.0.0.1", target.port or 80, timeout=timeout)
    conn.connect()
    sock = conn.sock  # http.client a volte lo stacca dalla connessione mentre legge: lo teniamo noi
    stop = threading.Event()
    state = {"interrupted": False}

    def watchdog() -> None:  # anche mentre llama-server legge l'immagine (nessun pezzo ancora arrivato)
        while not stop.wait(0.3):
            if should_stop():
                state["interrupted"] = True
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return

    threading.Thread(target=watchdog, daemon=True).start()
    text = ""
    try:
        conn.request("POST", target.path or "/v1/chat/completions", json.dumps(body), {"Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status != 200:
            raise OSError(f"llama-server ha risposto {resp.status}")
        for raw in resp:
            line = raw.decode(errors="replace").strip()
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            try:
                delta = json.loads(line[5:])["choices"][0].get("delta", {})
            except (ValueError, KeyError, IndexError):
                continue
            text += delta.get("content") or ""
    except (OSError, http.client.HTTPException):
        if not state["interrupted"]:
            raise
    finally:
        stop.set()
        conn.close()
    return text, state["interrupted"]
