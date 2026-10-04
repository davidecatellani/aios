"""Le impronte delle foto (SigLIP2): cercare le foto a parole, trovare quelle simili e i doppioni.

SigLIP2 (Google, Apache 2.0) mette foto e frasi nello stesso spazio: l'impronta di «il mare al tramonto»
è vicina a quella delle foto del mare al tramonto, in italiano come in altre lingue. Ogni foto si guarda
una volta sola, a riposo (circa un decimo di secondo); poi la ricerca è immediata e non serve averla
descritta prima. Due foto con impronte quasi uguali sono doppioni o scatti in sequenza.

Gira con onnxruntime, senza scheda video e senza torch: la parte per le foto pesa 95 MB, quella per le
frasi 283 MB (si carica solo quando si cerca e si libera dopo qualche minuto).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

MODEL_DIRS = [Path("/usr/share/aios/siglip2")]
MAX_TOKENS = 64
IMAGE_SIZE = 256
IDLE_UNLOAD = 300.0  # secondi senza ricerche dopo cui la parte per le frasi si libera
SIMILAR = 0.95  # somiglianza (coseno) sopra cui due foto sono doppioni o scatti in sequenza


def model_dir() -> Path | None:
    user = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "modelli" / "siglip2"
    for d in [user, *MODEL_DIRS]:
        if (d / "vision_model_int8.onnx").exists() and (d / "tokenizer.json").exists():
            return d
    return None


def available() -> bool:
    if model_dir() is None:
        return False
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError:
        return False
    return True


def _normalize(v: Any) -> list[float]:
    import numpy as np

    v = np.asarray(v, dtype=np.float32).reshape(-1)
    return (v / (np.linalg.norm(v) or 1.0)).tolist()


def load_pixels(path: Path | str) -> Any:
    """La foto come la vuole SigLIP2: 256×256, RGB, valori tra -1 e 1 (girata secondo l'EXIF)."""
    import cv2
    import numpy as np

    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"non riesco ad aprire {path}")
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, (IMAGE_SIZE, IMAGE_SIZE), interpolation=cv2.INTER_LINEAR)
    x = img.astype(np.float32) / 255.0
    x = (x - 0.5) / 0.5
    return x.transpose(2, 0, 1)[None]


class Siglip:
    """Le due metà di SigLIP2, caricate solo quando servono."""

    def __init__(self, folder: Path | None = None, threads: int = 2):
        self.folder = folder or model_dir()
        self.threads = threads
        self._vision: Any = None
        self._text: Any = None
        self._tok: Any = None
        self._text_used = 0.0
        self._lock = threading.Lock()

    def _session(self, name: str) -> Any:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.threads
        return ort.InferenceSession(str(self.folder / name), opts, providers=["CPUExecutionProvider"])

    def image(self, path: Path | str) -> list[float]:
        with self._lock:
            if self._vision is None:
                self._vision = self._session("vision_model_int8.onnx")
            out = self._vision.run(None, {self._vision.get_inputs()[0].name: load_pixels(path)})
        return _normalize(out[-1] if len(out) > 1 else out[0])

    def text(self, query: str) -> list[float]:
        import numpy as np

        with self._lock:
            if self._text is None:
                from tokenizers import Tokenizer

                self._tok = Tokenizer.from_file(str(self.folder / "tokenizer.json"))
                self._tok.enable_padding(length=MAX_TOKENS, pad_id=0)
                self._tok.enable_truncation(MAX_TOKENS)
                self._text = self._session("text_model_int8.onnx")
            ids = np.array([self._tok.encode(query.lower()).ids], dtype=np.int64)
            out = self._text.run(None, {self._text.get_inputs()[0].name: ids})
            self._text_used = time.monotonic()
        return _normalize(out[-1] if len(out) > 1 else out[0])

    def release_idle(self) -> None:
        """Libera la parte per le frasi se non si cerca da qualche minuto (e quella per le foto a fine lavoro)."""
        with self._lock:
            if self._text is not None and time.monotonic() - self._text_used > IDLE_UNLOAD:
                self._text = self._tok = None

    def release_images(self) -> None:
        with self._lock:
            self._vision = None


_shared: Siglip | None = None


def shared() -> Siglip:
    global _shared
    if _shared is None:
        _shared = Siglip()
    return _shared
