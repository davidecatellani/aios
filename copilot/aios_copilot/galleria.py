"""Riconoscere le foto, tutto sul computer: cosa c'è, le scritte, le persone.

Per ogni foto dell'utente (Immagini, foto dal telefono, Scaricati):
- **cosa c'è**: un modello di visione locale (MiniCPM-V, via Ollama) scrive una descrizione in italiano,
  delle etichette («mare», «torta», «cane») e le scritte che si leggono (scontrini, cartelli, schermate);
- **le persone**: OpenCV trova i volti (YuNet) e ne ricava un'impronta (SFace); i volti simili formano
  una persona. La prima volta l'utente dice chi è («Aurora»), poi Nova la riconosce da sola.

Il lavoro si fa solo a riposo e in carica (learning.py), una foto alla volta: appena l'utente torna, la
foto in corso si interrompe entro un paio di secondi e si riprende dopo. Tutto resta in
~/.local/share/aios/galleria.db; si spegne e si cancella da Impostazioni › Privacy o chiedendolo a Nova.
"""

from __future__ import annotations

import base64
import http.client
import json
import math
import os
import re
import socket
import sqlite3
import struct
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterable

IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
FACE_MATCH = 0.40  # somiglianza (coseno) sopra cui due volti sono la stessa persona (SFace: ~0,36)
FACE_MIN = 40  # lato minimo di un volto in pixel: più piccolo è solo rumore
VISION_MODEL = "minicpm-v4.6:1b"
MODEL_DIRS = [Path("/usr/share/aios/volti")]
CAPTION_SCHEMA = {"type": "object", "properties": {
    "descrizione": {"type": "string"}, "etichette": {"type": "array", "items": {"type": "string"}},
    "testo": {"type": "string"}}, "required": ["descrizione", "etichette", "testo"]}
CAPTION_PROMPT = ("Guarda la foto e rispondi in italiano con un JSON: «descrizione» (una frase: chi o cosa c'è, dove, "
                  "cosa succede), «etichette» (da 3 a 10 parole singole: oggetti, animali, luoghi, occasione, stagione) "
                  "e «testo» (le scritte leggibili nella foto, o stringa vuota). Non inventare nomi di persone.")


# --- impostazioni ------------------------------------------------------------------------------------------
def settings_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "galleria.json"


def enabled() -> bool:
    try:
        return bool(json.loads(settings_path().read_text()).get("attivo", False))
    except (OSError, ValueError, AttributeError):
        return False


def set_enabled(on: bool) -> None:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"attivo": bool(on)}))


def picture_roots(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    return [p for p in (home / "Immagini", home / "Pictures", home / "Scaricati", home / "Downloads",
                        home / "Telefono", home / "DCIM") if p.is_dir()]


def _plain(text: str) -> str:
    return (text.lower().replace("à", "a").replace("è", "e").replace("é", "e").replace("ì", "i")
            .replace("ò", "o").replace("ù", "u"))


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _normalize(vec: Iterable[float]) -> list[float]:
    v = list(vec)
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# --- l'archivio --------------------------------------------------------------------------------------------
class Gallery:
    def __init__(self, path: Path | None = None, roots: Callable[[], list[Path]] = picture_roots):
        if path is None:
            from .agenda import data_dir
            from .privacy import private_dir

            path = private_dir(data_dir()) / "galleria.db"
        self.path, self.roots = path, roots
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS foto (percorso TEXT PRIMARY KEY, mtime REAL, descrizione TEXT DEFAULT '',
                    etichette TEXT DEFAULT '', testo TEXT DEFAULT '', descritta INTEGER DEFAULT 0, volti_fatti INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS volti (id INTEGER PRIMARY KEY, percorso TEXT, x INTEGER, y INTEGER, w INTEGER,
                    h INTEGER, vettore BLOB, persona INTEGER);
                CREATE TABLE IF NOT EXISTS persone (id INTEGER PRIMARY KEY, nome TEXT DEFAULT '', centro BLOB, quanti INTEGER DEFAULT 0);
                CREATE INDEX IF NOT EXISTS volti_persona ON volti(persona);
            """)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self._scanned = 0.0

    # elenco delle foto
    def scan(self, every: float = 3600.0) -> int:
        """Aggiunge le foto nuove o cambiate e toglie quelle sparite (al massimo una volta ogni `every` s)."""
        if time.monotonic() - self._scanned < every and self._scanned:
            return 0
        self._scanned = time.monotonic()
        seen: dict[str, float] = {}
        for root in self.roots():
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    p = Path(dirpath) / f
                    if p.suffix.lower() in IMAGES and not f.startswith("."):
                        try:
                            seen[str(p)] = p.stat().st_mtime
                        except OSError:
                            pass
        with self.lock:
            known = dict(self.db.execute("SELECT percorso, mtime FROM foto"))
            added = 0
            for p, m in seen.items():
                if known.get(p) != m:
                    self.db.execute("INSERT OR REPLACE INTO foto (percorso, mtime) VALUES (?, ?)", (p, m))
                    self.db.execute("DELETE FROM volti WHERE percorso = ?", (p,))
                    added += 1
            for p in set(known) - set(seen):
                self.db.execute("DELETE FROM foto WHERE percorso = ?", (p,))
                self.db.execute("DELETE FROM volti WHERE percorso = ?", (p,))
            self.db.commit()
        return added

    def pending(self, column: str, limit: int = 1) -> list[str]:
        assert column in ("descritta", "volti_fatti")
        with self.lock:
            rows = self.db.execute(f"SELECT percorso FROM foto WHERE {column} = 0 ORDER BY mtime DESC LIMIT ?", (limit,))
            return [r[0] for r in rows]

    def store_caption(self, path: str, description: str, tags: list[str], text: str) -> None:
        with self.lock:
            self.db.execute("UPDATE foto SET descrizione = ?, etichette = ?, testo = ?, descritta = 1 WHERE percorso = ?",
                            (description[:400], ", ".join(t.strip().lower() for t in tags[:12] if t.strip()),
                             text[:400], path))
            self.db.commit()

    def skip(self, path: str, column: str) -> None:
        """Una foto che non si riesce a leggere: segnata, così non si riprova all'infinito."""
        assert column in ("descritta", "volti_fatti")
        with self.lock:
            self.db.execute(f"UPDATE foto SET {column} = 2 WHERE percorso = ?", (path,))
            self.db.commit()

    # persone
    def add_faces(self, path: str, faces: list[tuple[tuple[int, int, int, int], list[float]]]) -> list[int]:
        """Salva i volti di una foto e li assegna alla persona più simile (o a una persona nuova, senza nome)."""
        assigned = []
        with self.lock:
            people = [(pid, _unpack(c), n) for pid, c, n in self.db.execute("SELECT id, centro, quanti FROM persone")]
            for box, vec in faces:
                vec = _normalize(vec)
                best = max(((pid, _cos(vec, c), c, n) for pid, c, n in people), key=lambda t: t[1], default=None)
                if best is not None and best[1] >= FACE_MATCH:
                    pid, _, center, n = best
                    center = _normalize([(c * n + v) / (n + 1) for c, v in zip(center, vec)])
                    self.db.execute("UPDATE persone SET centro = ?, quanti = ? WHERE id = ?", (_pack(center), n + 1, pid))
                    people = [(p, center if p == pid else c, n + 1 if p == pid else k) for p, c, k in people]
                else:
                    pid = self.db.execute("INSERT INTO persone (centro, quanti) VALUES (?, 1)", (_pack(vec),)).lastrowid
                    people.append((pid, vec, 1))
                self.db.execute("INSERT INTO volti (percorso, x, y, w, h, vettore, persona) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (path, *box, _pack(vec), pid))
                assigned.append(pid)
            self.db.execute("UPDATE foto SET volti_fatti = 1 WHERE percorso = ?", (path,))
            self.db.commit()
        return assigned

    def name_person(self, pid: int, name: str) -> str:
        """Dà un nome a una persona; se il nome c'è già, le due persone diventano una."""
        name = re.sub(r"\s+", " ", name).strip()[:60]
        with self.lock:
            other = self.db.execute("SELECT id, centro, quanti FROM persone WHERE lower(nome) = lower(?) AND id != ?",
                                    (name, pid)).fetchone()
            if other and name:
                me = self.db.execute("SELECT centro, quanti FROM persone WHERE id = ?", (pid,)).fetchone()
                if me:
                    a, na, b, nb = _unpack(other[1]), other[2], _unpack(me[0]), me[1]
                    center = _normalize([(x * na + y * nb) / (na + nb) for x, y in zip(a, b)])
                    self.db.execute("UPDATE persone SET centro = ?, quanti = ? WHERE id = ?", (_pack(center), na + nb, other[0]))
                    self.db.execute("UPDATE volti SET persona = ? WHERE persona = ?", (other[0], pid))
                    self.db.execute("DELETE FROM persone WHERE id = ?", (pid,))
            else:
                self.db.execute("UPDATE persone SET nome = ? WHERE id = ?", (name, pid))
            self.db.commit()
        return name

    def people(self, min_faces: int = 2) -> list[dict[str, Any]]:
        """Le persone trovate: con nome prima, poi quelle senza nome con più foto (le più utili da nominare)."""
        with self.lock:
            rows = self.db.execute(
                "SELECT p.id, p.nome, COUNT(v.id), MIN(v.id) FROM persone p JOIN volti v ON v.persona = p.id "
                "GROUP BY p.id HAVING COUNT(v.id) >= ? OR p.nome != '' ORDER BY (p.nome = ''), COUNT(v.id) DESC",
                (min_faces,)).fetchall()
        return [{"id": r[0], "nome": r[1], "foto": r[2], "volto": r[3]} for r in rows]

    def face(self, face_id: int) -> tuple[str, tuple[int, int, int, int]] | None:
        with self.lock:
            row = self.db.execute("SELECT percorso, x, y, w, h FROM volti WHERE id = ?", (face_id,)).fetchone()
        return (row[0], tuple(row[1:])) if row else None

    # ricerca
    def search(self, query: str, limit: int = 60) -> list[tuple[int, str]]:
        """(punteggio, percorso) per le foto che corrispondono: persone per nome, poi descrizione, etichette, scritte."""
        words = [w for w in re.findall(r"[a-z0-9]+", _plain(query)) if len(w) > 2]
        if not words:
            return []
        scores: dict[str, int] = {}
        with self.lock:
            for pid, nome in self.db.execute("SELECT id, nome FROM persone WHERE nome != ''"):
                if any(w in _plain(nome).split() for w in words):
                    for (p,) in self.db.execute("SELECT DISTINCT percorso FROM volti WHERE persona = ?", (pid,)):
                        scores[p] = scores.get(p, 0) + 3
            for p, d, e, t in self.db.execute("SELECT percorso, descrizione, etichette, testo FROM foto WHERE descritta = 1"):
                hay = _plain(f"{d} {e} {t}")
                s = sum(2 if re.search(rf"\b{re.escape(w)}", _plain(e)) else 1 for w in words if w in hay)
                if s:
                    scores[p] = scores.get(p, 0) + s
        return sorted(((s, p) for p, s in scores.items()), key=lambda t: -t[0])[:limit]

    def stats(self) -> dict[str, int]:
        with self.lock:
            total, described, faced = self.db.execute(
                "SELECT COUNT(*), SUM(descritta != 0), SUM(volti_fatti != 0) FROM foto").fetchone()
            named = self.db.execute("SELECT COUNT(*) FROM persone WHERE nome != ''").fetchone()[0]
        return {"foto": total or 0, "descritte": described or 0, "volti_fatti": faced or 0, "persone_con_nome": named}

    def forget(self) -> None:
        with self.lock:
            self.db.executescript("DELETE FROM foto; DELETE FROM volti; DELETE FROM persone;")
            self.db.commit()


# --- immagini --------------------------------------------------------------------------------------------
def small_jpeg(path: Path | str, size: int = 768, box: tuple[int, int, int, int] | None = None) -> bytes | None:
    """La foto (o un riquadro) rimpicciolita in JPEG, girata secondo l'EXIF."""
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        pix = GdkPixbuf.Pixbuf.new_from_file(str(path))
        pix = pix.apply_embedded_orientation() or pix
        if box:
            x, y, w, h = box
            pad = int(max(w, h) * 0.25)
            x0, y0 = max(0, x - pad), max(0, y - pad)
            x1, y1 = min(pix.get_width(), x + w + pad), min(pix.get_height(), y + h + pad)
            pix = pix.new_subpixbuf(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
        scale = min(1.0, size / max(pix.get_width(), pix.get_height()))
        if scale < 1.0:
            pix = pix.scale_simple(max(1, int(pix.get_width() * scale)), max(1, int(pix.get_height() * scale)),
                                   GdkPixbuf.InterpType.BILINEAR)
        ok, data = pix.save_to_bufferv("jpeg", ["quality"], ["85"])
        return bytes(data) if ok else None
    except Exception:
        return None


def model_file(name: str) -> Path | None:
    for d in MODEL_DIRS:
        found = sorted(d.glob(f"{name}*.onnx")) if d.is_dir() else []
        if found:
            return found[-1]
    return None


class FaceFinder:
    """Volti e impronte con OpenCV (YuNet + SFace, modelli ONNX inclusi nell'immagine)."""

    def __init__(self) -> None:
        import cv2  # type: ignore

        det, rec = model_file("face_detection_yunet"), model_file("face_recognition_sface")
        if det is None or rec is None:
            raise RuntimeError("mancano i modelli dei volti")
        self.cv2 = cv2
        self.detector = cv2.FaceDetectorYN.create(str(det), "", (320, 320), 0.85)
        self.recognizer = cv2.FaceRecognizerSF.create(str(rec), "")

    def faces(self, path: str) -> list[tuple[tuple[int, int, int, int], list[float]]]:
        cv2 = self.cv2
        img = cv2.imread(path)  # applica l'orientamento EXIF, come GdkPixbuf
        if img is None:
            raise ValueError("immagine illeggibile")
        h, w = img.shape[:2]
        scale = min(1.0, 1280 / max(h, w))
        small = cv2.resize(img, (int(w * scale), int(h * scale))) if scale < 1 else img
        self.detector.setInputSize((small.shape[1], small.shape[0]))
        _, found = self.detector.detect(small)
        out = []
        for f in found if found is not None else []:
            x, y, fw, fh = (int(v / scale) for v in f[:4])
            if min(fw, fh) < FACE_MIN:
                continue
            aligned = self.recognizer.alignCrop(small, f)
            vec = self.recognizer.feature(aligned).flatten().tolist()
            out.append(((max(0, x), max(0, y), fw, fh), vec))
        return out


def faces_available() -> bool:
    try:
        import cv2  # type: ignore  # noqa: F401
    except Exception:
        return False
    return model_file("face_detection_yunet") is not None and model_file("face_recognition_sface") is not None


# --- descrizione con il modello di visione (interrompibile) --------------------------------------------
class Captioner:
    """Chiede la descrizione a Ollama; se l'utente torna (nessun passo da `patience` secondi) chiude la
    connessione e Ollama interrompe il calcolo. → dict o None (interrotta o non riuscita)."""

    def __init__(self, model: str, url: str | None = None, patience: float = 3.0):
        self.model = model
        self.url = urllib.parse.urlparse(url or os.environ.get("AIOS_OLLAMA_URL", "http://localhost:11434"))
        self.patience = patience
        self.alive = time.monotonic()
        self.result: dict[str, Any] | None = None
        self.error = ""
        self.interrupted = False
        self.unreachable = False
        self.thread: threading.Thread | None = None

    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, image: bytes) -> None:
        self.result, self.error, self.alive = None, "", time.monotonic()
        self.interrupted = self.unreachable = False
        self.thread = threading.Thread(target=self._run, args=(image,), daemon=True)
        self.thread.start()

    def poke(self) -> None:
        self.alive = time.monotonic()

    def _run(self, image: bytes) -> None:
        payload = json.dumps({"model": self.model, "stream": True, "think": False, "keep_alive": "5m",
                              "format": CAPTION_SCHEMA, "options": {"temperature": 0.1, "num_predict": 300},
                              "messages": [{"role": "user", "content": CAPTION_PROMPT,
                                            "images": [base64.b64encode(image).decode()]}]})
        conn = http.client.HTTPConnection(self.url.hostname or "localhost", self.url.port or 11434, timeout=900)
        stop = threading.Event()

        def watchdog() -> None:
            while not stop.wait(1.0):
                if time.monotonic() - self.alive > self.patience and conn.sock is not None:
                    self.interrupted = True
                    try:
                        conn.sock.shutdown(socket.SHUT_RDWR)  # Ollama vede la chiusura e smette di calcolare
                    except OSError:
                        pass
                    return

        threading.Thread(target=watchdog, daemon=True).start()
        try:
            conn.request("POST", "/api/chat", payload, {"Content-Type": "application/json"})
            resp = conn.getresponse()
            text = ""
            for line in resp:
                if line.strip():
                    text += json.loads(line).get("message", {}).get("content", "")
            data = json.loads(text)
            self.result = data if isinstance(data, dict) else None
        except (ConnectionRefusedError, socket.timeout) as exc:
            self.unreachable, self.error = True, str(exc) or exc.__class__.__name__
        except Exception as exc:
            self.error = str(exc) or exc.__class__.__name__
        finally:
            stop.set()
            conn.close()


def vision_model() -> str | None:
    """Il modello di visione pronto (quello scelto in AIOS, o MiniCPM-V se è già scaricato)."""
    try:
        from . import engines

        chosen = engines.available().get("vista")
        if chosen:
            return chosen
    except Exception:
        pass
    try:
        conn = http.client.HTTPConnection("localhost", 11434, timeout=5)
        conn.request("GET", "/api/tags")
        names = [m.get("name", "") for m in json.loads(conn.getresponse().read()).get("models", [])]
        return next((n for n in names if n.startswith(VISION_MODEL.split(":")[0])), None)
    except Exception:
        return None


# --- il lavoro a riposo ----------------------------------------------------------------------------------
class GalleryTask:
    """Per learning.py: a riposo e in carica, una foto alla volta (prima i volti, veloci, poi le descrizioni)."""

    name = "riconoscimento delle foto"

    def __init__(self, gallery: Gallery | None = None, faces: Callable[[], Any] | None = None,
                 captioner: Callable[[str], Captioner] | None = None, model: Callable[[], str | None] = vision_model):
        self._gallery = gallery
        self._faces_factory = faces or FaceFinder
        self._faces: Any = None
        self._make_captioner = captioner or (lambda m: Captioner(m))
        self._captioner: Captioner | None = None
        self._current = ""
        self._model_source = model
        self._model_cache: tuple[float, str | None] = (0.0, None)
        self._retry_at = 0.0

    def model(self) -> str | None:
        """Il modello di visione, ricontrollato ogni 10 minuti (non a ogni passo)."""
        at, name = self._model_cache
        if time.monotonic() - at > 600 or not at:
            name = self._model_source()
            self._model_cache = (time.monotonic(), name)
        return name

    @property
    def gallery(self) -> Gallery:
        if self._gallery is None:
            self._gallery = Gallery()
        return self._gallery

    def available(self) -> bool:
        return enabled() and time.monotonic() >= self._retry_at

    def has_work(self) -> bool:
        if not enabled():
            return False
        self.gallery.scan()
        return bool(self.gallery.pending("volti_fatti") or (self.model() and self.gallery.pending("descritta")))

    def step(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        # 1) i volti: pochi decimi di secondo per foto
        while time.monotonic() < end:
            todo = self.gallery.pending("volti_fatti")
            if not todo:
                break
            try:
                if self._faces is None:
                    self._faces = self._faces_factory()
                self.gallery.add_faces(todo[0], self._faces.faces(todo[0]))
            except (RuntimeError, ImportError):
                self._faces = None
                self.gallery.skip(todo[0], "volti_fatti")  # OpenCV o modelli assenti: niente volti
            except Exception:
                self.gallery.skip(todo[0], "volti_fatti")
        if time.monotonic() >= end:
            return
        # 2) le descrizioni: lente (decine di secondi), in un thread che si interrompe se l'utente torna
        cap = self._captioner
        if cap is not None and cap.busy():
            cap.poke()
            return
        if cap is not None and self._current:
            if cap.result:
                r = cap.result
                self.gallery.store_caption(self._current, str(r.get("descrizione", "")),
                                           [str(t) for t in r.get("etichette", []) if isinstance(t, (str, int))],
                                           str(r.get("testo", "")))
            elif cap.unreachable:
                self._retry_at = time.monotonic() + 600  # Ollama non risponde: si riprova più tardi
            elif not cap.interrupted:
                self.gallery.skip(self._current, "descritta")  # la risposta non era valida: si passa oltre
            self._current = ""  # interrotta perché l'utente è tornato: la foto resta da fare
            return
        model = self.model()
        todo = self.gallery.pending("descritta")
        if not model or not todo:
            return
        image = small_jpeg(todo[0])
        if image is None:
            self.gallery.skip(todo[0], "descritta")
            return
        self._captioner = self._make_captioner(model)
        self._current = todo[0]
        self._captioner.start(image)
