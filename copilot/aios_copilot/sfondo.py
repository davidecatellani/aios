"""Togliere lo sfondo da una foto: il soggetto (persona, oggetto, animale) resta, il resto diventa trasparente
o di un colore a scelta (bianco per una fototessera, per esempio).

Modello: BiRefNet lite (licenza MIT) in ONNX, 224 MB, gira con onnxruntime sul processore in un paio di secondi.
Sta in /usr/share/aios/birefnet (immagine di SoIA) o in ~/.local/share/aios/modelli/birefnet. Si carica alla
prima richiesta e si libera dopo qualche minuto.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

SIZE = 1024
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
IDLE_UNLOAD = 300.0
COLORS = {"": None, "trasparente": None, "bianco": (255, 255, 255), "nero": (0, 0, 0), "grigio": (235, 235, 235),
          "azzurro": (205, 230, 245), "blu": (40, 90, 160), "verde": (0, 177, 64), "rosso": (200, 30, 40)}


def model_path() -> Path | None:
    user = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "modelli" / "birefnet" / "model.onnx"
    for p in (user, Path("/usr/share/aios/birefnet/model.onnx")):
        if p.exists():
            return p
    return None


def available() -> bool:
    if model_path() is None:
        return False
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


class Remover:
    def __init__(self, path: Path | None = None, threads: int = 0):
        self.path = path or model_path()
        self.threads = threads or max(1, (os.cpu_count() or 2) - 1)
        self._session: Any = None
        self._used = 0.0
        self._lock = threading.Lock()

    def _load(self) -> Any:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.threads
        providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
        return ort.InferenceSession(str(self.path), opts, providers=providers)

    def mask(self, rgb: Any) -> Any:
        """Immagine RGB (altezza × larghezza × 3, uint8) → maschera 0…1 della stessa misura."""
        import cv2
        import numpy as np

        h, w = rgb.shape[:2]
        x = cv2.resize(rgb, (SIZE, SIZE), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
        x = (x - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)
        x = x.transpose(2, 0, 1)[None]
        with self._lock:
            if self._session is None:
                self._session = self._load()
            out = self._session.run(None, {self._session.get_inputs()[0].name: x})[0][0, 0]
            self._used = time.monotonic()
        if out.min() < 0 or out.max() > 1:  # logit → probabilità
            out = 1 / (1 + np.exp(-out))
        return cv2.resize(out.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)

    def release_idle(self) -> None:
        with self._lock:
            if self._session is not None and time.monotonic() - self._used > IDLE_UNLOAD:
                self._session = None


def cut(remover: Remover, src: Path, dest: Path | None = None, background: str = "", crop: bool = False) -> Path:
    """Toglie lo sfondo da «src» e salva un PNG (trasparente o col colore scelto). → il file nuovo."""
    import cv2
    import numpy as np

    bgr = cv2.imread(str(src), cv2.IMREAD_COLOR)  # applica anche la rotazione EXIF delle foto del telefono
    if bgr is None:
        raise ValueError(f"non riesco ad aprire «{src.name}» come immagine")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    m = remover.mask(rgb)
    alpha = np.clip(m * 255.0, 0, 255).astype(np.uint8)
    if crop:  # solo il soggetto, con un piccolo margine
        ys, xs = np.where(alpha > 32)
        if len(xs):
            pad = int(0.03 * max(rgb.shape[:2]))
            y0, y1 = max(0, ys.min() - pad), min(rgb.shape[0], ys.max() + pad + 1)
            x0, x1 = max(0, xs.min() - pad), min(rgb.shape[1], xs.max() + pad + 1)
            bgr, alpha = bgr[y0:y1, x0:x1], alpha[y0:y1, x0:x1]
    color = COLORS.get(background.lower().strip(), None) if not background.startswith("#") else \
        tuple(int(background[i:i + 2], 16) for i in (1, 3, 5))
    if color is None:
        out = np.dstack([bgr, alpha])
    else:
        a = alpha[..., None].astype(np.float32) / 255.0
        bg = np.array(color[::-1], dtype=np.float32)  # RGB → BGR
        out = (bgr.astype(np.float32) * a + bg * (1 - a)).astype(np.uint8)
    dest = dest or unique(src.with_name(f"{src.stem} senza sfondo.png"))
    if not cv2.imwrite(str(dest), out):
        raise OSError(f"non riesco a salvare {dest}")
    return dest


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
    return path


_shared: Remover | None = None


def shared() -> Remover:
    global _shared
    if _shared is None:
        _shared = Remover()
        r = _shared

        def janitor() -> None:
            while True:
                time.sleep(60)
                r.release_idle()

        threading.Thread(target=janitor, daemon=True).start()
    return _shared
