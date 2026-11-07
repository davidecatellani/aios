"""Sottotitoli in tempo reale di tutto quello che il PC fa sentire (video, chiamate, giochi), come i «Sottotitoli
dal vivo» di Windows e macOS: tutto sul computer, con Parakeet (lo stesso modello dell'ascolto di Nova, che capisce
italiano, inglese e altre lingue europee).

L'audio si prende dal «monitor» dell'uscita in uso (parec, 16 kHz mono). La frase che si sta formando si
ritrascrive ogni secondo e mezzo (sottotitolo provvisorio); con una pausa, o dopo 8 secondi, diventa definitiva.
Il testo lo mostra la shell in una striscia in basso, sopra i programmi (shell/__init__.py).
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Iterator

RATE = 16000
CHUNK = RATE // 5 * 2  # 0,2 s di audio a 16 bit
SILENCE = 350.0  # sotto questo volume medio è pausa
PAUSE = 0.6  # secondi di pausa che chiudono una frase
MAX_PHRASE = 8.0
REFRESH = 1.5


def capture_command(which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    if which("parec"):
        return ["parec", "-d", "@DEFAULT_MONITOR@", "--format=s16le", f"--rate={RATE}", "--channels=1", "--raw", "--latency-msec=100"]
    if which("pw-record"):
        return ["pw-record", "-P", "stream.capture.sink=true", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"]
    return None


def chunks_of(cmd: list[str], stop: threading.Event) -> Iterator[bytes]:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert proc.stdout is not None
        while not stop.is_set():
            data = proc.stdout.read(CHUNK)
            if not data:
                return
            yield data
    finally:
        proc.kill()


class Captioner:
    def __init__(self, show: Callable[[str, bool], None], transcribe: Callable[[bytes], str] | None = None,
                 source: Callable[[threading.Event], Iterator[bytes]] | None = None, clock: Callable[[], float] = time.monotonic):
        self.show, self.clock = show, clock
        self._transcribe = transcribe
        self.source = source
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def transcribe(self, pcm: bytes) -> str:
        if self._transcribe is None:
            from . import parakeet

            self._transcribe = parakeet.shared().transcribe
        return self._transcribe(pcm)

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> bool:
        if self.running:
            return True
        if self.source is None:
            cmd = capture_command()
            if cmd is None:
                return False
            self.source = lambda stop: chunks_of(cmd, stop)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        return True

    def stop(self) -> None:
        self.stop_event.set()
        self.show("", True)

    def loop(self) -> None:
        from .voice import loudness

        assert self.source is not None
        phrase, quiet_since, last_draft, started = b"", None, 0.0, None
        for data in self.source(self.stop_event):
            now = self.clock()
            loud = loudness(data) > SILENCE
            if loud:
                quiet_since = None
                if not phrase:
                    started = now
                phrase += data
            elif phrase:
                phrase += data
                quiet_since = quiet_since or now
            if not phrase:
                continue
            ended = (quiet_since is not None and now - quiet_since >= PAUSE) or (started is not None and now - started >= MAX_PHRASE)
            if ended:
                text = self.transcribe(phrase)
                if text:
                    self.show(text, True)
                phrase, quiet_since, started = b"", None, None
            elif now - last_draft >= REFRESH and len(phrase) > RATE:  # almeno mezzo secondo di voce
                text = self.transcribe(phrase)
                if text:
                    self.show(text, False)
                last_draft = now


_shared: dict[str, Any] = {}


def toggle(on: bool, show: Callable[[str, bool], None]) -> bool:
    cap = _shared.get("c")
    if on:
        if cap is None:
            cap = _shared["c"] = Captioner(show)
        return cap.start()
    if cap is not None:
        cap.stop()
    return True
