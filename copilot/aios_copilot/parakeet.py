"""Trascrivere bene quello che si dice a Nova: Parakeet TDT 0.6B v3 (NVIDIA, CC-BY 4.0), tutto sul computer.

Vosk, piccolo e velocissimo, resta il «guardiano»: si accorge che qualcuno parla e quando la frase finisce
(e riconosce la voce, voiceprint.py). La frase intera, una volta finita, la trascrive Parakeet: molti meno
errori in italiano, punteggiatura compresa, in circa mezzo secondo per frase su un processore normale.
Così Nova capisce meglio cosa le si chiede e scarta meglio quello che non è per lei.

Il modello (ONNX int8, 670 MB) si carica alla prima frase e si libera dopo qualche minuto di silenzio.
Gira con sherpa-onnx; se manca, la trascrizione resta quella di Vosk.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

MODEL_DIRS = [Path("/usr/share/aios/parakeet")]
FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
RATE = 16000
IDLE_UNLOAD = 600.0


def model_dir() -> Path | None:
    user = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "modelli" / "parakeet"
    for d in [user, *MODEL_DIRS]:
        if all((d / f).exists() for f in FILES):
            return d
    return None


def available() -> bool:
    if os.environ.get("AIOS_PARAKEET", "") == "spento" or model_dir() is None:
        return False
    try:
        import numpy  # noqa: F401
        import sherpa_onnx  # noqa: F401
    except ImportError:
        return False
    return True


class Parakeet:
    def __init__(self, folder: Path | None = None, threads: int = 2):
        self.folder = folder or model_dir()
        self.threads = threads
        self._rec: Any = None
        self._used = 0.0
        self._lock = threading.Lock()

    def _load(self) -> Any:
        import sherpa_onnx

        d = self.folder
        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(d / FILES[0]), decoder=str(d / FILES[1]), joiner=str(d / FILES[2]), tokens=str(d / FILES[3]),
            num_threads=self.threads, model_type="nemo_transducer", decoding_method="greedy_search")

    def transcribe(self, pcm: bytes) -> str:
        """Audio a 16 kHz, 16 bit, mono → testo."""
        import numpy as np

        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size < RATE // 4:
            return ""
        with self._lock:
            if self._rec is None:
                self._rec = self._load()
            stream = self._rec.create_stream()
            stream.accept_waveform(RATE, samples)
            self._rec.decode_stream(stream)
            self._used = time.monotonic()
            return str(stream.result.text).strip()

    def release_idle(self) -> None:
        with self._lock:
            if self._rec is not None and time.monotonic() - self._used > IDLE_UNLOAD:
                self._rec = None


_shared: Parakeet | None = None


def shared() -> Parakeet:
    """Il Parakeet dell'ascolto, che si libera da solo dopo IDLE_UNLOAD secondi senza frasi."""
    global _shared
    if _shared is None:
        _shared = Parakeet()
        model = _shared

        def janitor() -> None:
            while True:
                time.sleep(60)
                model.release_idle()

        threading.Thread(target=janitor, daemon=True).start()
    return _shared
