"""La voce di Nova: Kokoro 82M (Apache 2.0), voci italiane naturali «Sara» e «Nicola», tutto sul computer.

Molto più naturale di Piper, ma più lento: per non far aspettare, il testo si divide in frasi e Nova comincia
a parlare appena la prima è pronta, mentre prepara le successive. Gira con onnxruntime (kokoro-onnx, modello
int8 di 92 MB); il modello resta caricato nel processo che parla e si libera dopo 20 minuti di silenzio.
"""

from __future__ import annotations

import os
import queue
import re
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable

MODEL_DIRS = [Path("/usr/share/aios/kokoro")]
MODEL, VOICES = "kokoro-v1.0.int8.onnx", "voices-v1.0.bin"
NAMES = {"if_sara": "Sara (naturale)", "im_nicola": "Nicola (naturale)"}
IDLE_UNLOAD = 1200.0  # 20 minuti: la prima risposta dopo una pausa breve non aspetta


def model_dir() -> Path | None:
    user = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "modelli" / "kokoro"
    for d in [user, *MODEL_DIRS]:
        if (d / MODEL).exists() and (d / VOICES).exists():
            return d
    return None


def available() -> bool:
    if os.environ.get("AIOS_KOKORO", "") == "spento" or model_dir() is None:
        return False
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    return True


def sentences(text: str, shortest: int = 40) -> list[str]:
    """Il testo in frasi da dire una alla volta (le brevissime unite alla successiva)."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?;:])\s+|\n+", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        if len(out) > 1 and len(out[-1]) < shortest:  # la prima resta da sola: Nova comincia a parlare prima
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


class Voice:
    def __init__(self, folder: Path | None = None):
        self.folder = folder or model_dir()
        self._k: Any = None
        self._used = 0.0
        self._lock = threading.Lock()

    def synth(self, text: str, voice: str, speed: float = 1.0) -> tuple[Any, int]:
        with self._lock:
            if self._k is None:
                from kokoro_onnx import Kokoro

                self._k = Kokoro(str(self.folder / MODEL), str(self.folder / VOICES))
            samples, rate = self._k.create(text, voice=voice, speed=speed, lang="it")
            self._used = time.monotonic()
            return samples, rate

    def release_idle(self) -> None:
        with self._lock:
            if self._k is not None and time.monotonic() - self._used > IDLE_UNLOAD:
                self._k = None


def warm_up() -> None:
    """Carica il modello in anticipo (all'avvio della shell), così la prima risposta non aspetta."""
    if available():
        try:
            shared().synth("Ciao.", "if_sara")
        except Exception:
            pass


def write_wav(samples: Any, rate: int) -> Path:
    import numpy as np

    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1, 1) * 32767).astype(np.int16).tobytes()
    fd, name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(name, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return Path(name)


def speak(text: str, voice: str, play: Callable[[Path], None], synth: Callable[[str, str], tuple[Any, int]] | None = None) -> bool:
    """Dice il testo frase per frase: la prima comincia appena pronta, le altre si preparano mentre parla."""
    synth = synth or shared().synth
    parts = sentences(text)
    if not parts:
        return False
    ready: queue.Queue[Path | None] = queue.Queue(maxsize=2)

    def produce() -> None:
        try:
            for part in parts:
                samples, rate = synth(part, voice)
                ready.put(write_wav(samples, rate))
        except Exception:
            pass
        finally:
            ready.put(None)

    threading.Thread(target=produce, daemon=True).start()
    spoke = False
    while (wav := ready.get()) is not None:
        try:
            play(wav)
            spoke = True
        finally:
            wav.unlink(missing_ok=True)
    return spoke


_shared: Voice | None = None


def shared() -> Voice:
    global _shared
    if _shared is None:
        _shared = Voice()
        v = _shared

        def janitor() -> None:
            while True:
                time.sleep(60)
                v.release_idle()

        threading.Thread(target=janitor, daemon=True).start()
    return _shared
