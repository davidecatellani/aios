"""Foto e video della fotocamera del telefono, salvati da soli sul PC.

- Solo la **fotocamera** (DCIM/Camera e simili): niente WhatsApp, Telegram, screenshot
  o immagini scaricate.
- Solo ciò che **manca** sul PC; copie interrotte riprese la volta dopo; l'originale
  sul telefono non viene mai toccato (accesso in sola lettura).
- Ordinate per data di scatto: Immagini/Telefono/2026/10, Video/Telefono/2026/10.
- Il telefono si legge tramite KDE Connect (SFTP montato dal demone): funziona con
  Android; per l'iPhone c'è il pulsante «Invia foto al PC» nella pagina «Il mio PC».
- «Migliora le foto»: con il modello Real-ESRGAN se installato (più nitide e grandi),
  altrimenti con correzioni automatiche (livelli, rumore, nitidezza). L'originale resta.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..privacy import private_dir
from ..xdg import resolve_folder

PHOTO_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".dng", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".3gp", ".webm", ".mkv"}
# Cartelle della fotocamera (Android, Samsung, Xiaomi, iPhone importato…); tutto il resto è escluso.
CAMERA_DIRS = ("DCIM/Camera", "DCIM/100ANDRO", "DCIM/100MEDIA", "DCIM/OpenCamera", "DCIM/100APPLE")
EXCLUDED_PARTS = {"whatsapp", "telegram", "screenshots", "screenshot", ".thumbnails", "signal", "instagram", "messenger"}
SFTP_IFACE = "org.kde.kdeconnect.device.sftp"


def camera_files(root: Path, max_depth: int = 4) -> list[Path]:
    """Foto e video scattati con la fotocamera, sotto la radice del telefono montato."""
    found = []
    seen_dirs = set()
    for depth in range(max_depth):
        pattern = "/".join(["*"] * depth)
        for cam in CAMERA_DIRS:
            for folder in root.glob(f"{pattern}/{cam}" if pattern else cam):
                real = folder.resolve()
                if real in seen_dirs or not folder.is_dir():
                    continue
                seen_dirs.add(real)
                for f in folder.rglob("*"):
                    parts = {p.lower() for p in f.relative_to(folder).parts}
                    if f.is_file() and f.suffix.lower() in PHOTO_EXT | VIDEO_EXT and not parts & EXCLUDED_PARTS \
                            and not f.name.startswith("."):
                        found.append(f)
    return sorted(found)


class PhotoSync:
    def __init__(self, db_path: Path | None = None, pictures: Path | None = None, videos: Path | None = None):
        base = private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios")
        self.db = sqlite3.connect(db_path or base / "foto-telefono.db", check_same_thread=False, isolation_level=None)
        self.db.execute("CREATE TABLE IF NOT EXISTS imported (phone TEXT, source TEXT, size INTEGER, dest TEXT, at REAL,"
                        " PRIMARY KEY (phone, source, size))")
        self.pictures = pictures or resolve_folder("PICTURES") / "Telefono"
        self.videos = videos or resolve_folder("VIDEOS") / "Telefono"

    def destination(self, f: Path) -> Path:
        from ..metadata import photo_date

        # data dello scatto: EXIF, poi il nome (IMG_20261003_…, VID_20261003_…), poi la data del file
        when = photo_date(f) or datetime.fromtimestamp(f.stat().st_mtime)
        base = self.videos if f.suffix.lower() in VIDEO_EXT else self.pictures
        return base / f"{when:%Y}" / f"{when:%m}" / f.name

    def pending(self, phone: str, root: Path) -> list[Path]:
        out = []
        for f in camera_files(root):
            rel, size = str(f.relative_to(root)), f.stat().st_size
            if not self.db.execute("SELECT 1 FROM imported WHERE phone = ? AND source = ? AND size = ?", (phone, rel, size)).fetchone():
                out.append(f)
        return out

    def run(self, phone: str, root: Path, deadline: float | None = None,
            progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
        """Copia ciò che manca (fino alla scadenza, poi riprende la volta dopo)."""
        todo = self.pending(phone, root)
        copied, photos, videos, skipped = [], 0, 0, 0
        for i, f in enumerate(todo):
            if deadline and time.monotonic() > deadline:
                break
            dest = self.destination(f)
            size = f.stat().st_size
            if dest.exists() and dest.stat().st_size == size:
                skipped += 1  # già sul PC (es. copiata a mano)
            else:
                if dest.exists():  # stesso nome, file diverso: non si sovrascrive mai
                    dest = dest.with_name(f"{dest.stem} ({int(time.time())}){dest.suffix}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".parziale")
                shutil.copyfile(f, tmp)
                os.utime(tmp, (f.stat().st_atime, f.stat().st_mtime))
                tmp.rename(dest)
                copied.append(dest)
                if f.suffix.lower() in VIDEO_EXT:
                    videos += 1
                else:
                    photos += 1
            self.db.execute("INSERT OR REPLACE INTO imported VALUES (?, ?, ?, ?, ?)",
                            (phone, str(f.relative_to(root)), size, str(dest), time.time()))
            if progress:
                progress(i + 1, len(todo))
        remaining = len(todo) - (photos + videos + skipped)
        if copied:
            self.db.execute("CREATE TABLE IF NOT EXISTS last_batch (dest TEXT)")
            self.db.execute("DELETE FROM last_batch")
            self.db.executemany("INSERT INTO last_batch VALUES (?)", [(str(c),) for c in copied])
        return {"foto": photos, "video": videos, "gia_presenti": skipped, "mancanti": remaining, "copiati": copied}

    def last_batch(self) -> list[Path]:
        try:
            return [Path(r[0]) for r in self.db.execute("SELECT dest FROM last_batch")]
        except sqlite3.OperationalError:
            return []


def describe(result: dict[str, Any], phone_name: str) -> str:
    if not result["foto"] and not result["video"] and not result["mancanti"]:
        return f"Tutte le foto e i video della fotocamera di {phone_name} sono già sul PC. 👍"
    parts = []
    if result["foto"]:
        parts.append(f"{result['foto']} foto")
    if result["video"]:
        parts.append(f"{result['video']} video")
    text = f"Salvati sul PC {' e '.join(parts)} dalla fotocamera di {phone_name} (nelle cartelle Immagini e Video, sotto «Telefono»)."
    if result["mancanti"]:
        text += f" Ne mancano {result['mancanti']}: continuo appena posso."
    return text + " WhatsApp e screenshot esclusi."


def mount_phone(bus: Any, device: str) -> Path | None:
    """Monta (in sola lettura per AIOS) la memoria del telefono tramite KDE Connect."""
    path = f"/modules/kdeconnect/devices/{device}/sftp"
    if not bus._call(path, SFTP_IFACE, "mountAndWait"):
        return None
    data = bus._call(path, SFTP_IFACE, "mountPoint")
    point = Path(str(data[0])) if data else None
    return point if point and point.is_dir() else None


# --- migliorare le foto -----------------------------------------------------------------------------

FFMPEG_ENHANCE = "hqdn3d=1.2:1.2:4:4,normalize=strength=0.6:independence=0,unsharp=5:5:0.7:3:3:0,eq=saturation=1.06"


def enhance(photo: Path, run: Callable[[list[str]], tuple[int, str]] | None = None,
            which: Callable[[str], str | None] = shutil.which) -> tuple[Path | None, str]:
    """Versione migliorata accanto all'originale. → (file, metodo) oppure (None, motivo)."""
    run = run or _run
    if photo.suffix.lower() not in PHOTO_EXT:
        return None, "non è una foto"
    out = photo.with_name(f"{photo.stem} (migliorata).jpg")
    if which("realesrgan-ncnn-vulkan"):
        png = photo.with_name(f".{photo.stem}.aios.png")
        code, msg = run(["realesrgan-ncnn-vulkan", "-i", str(photo), "-o", str(png), "-n", "realesrgan-x4plus", "-s", "2"])
        if code == 0:
            code, msg = run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(png), "-q:v", "2", str(out)])
            png.unlink(missing_ok=True)
            if code == 0:
                return out, "Real-ESRGAN (AI, doppia risoluzione)"
    if not which("ffmpeg"):
        return None, "serve ffmpeg"
    code, msg = run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(photo), "-vf", FFMPEG_ENHANCE, "-q:v", "2", str(out)])
    return (out, "correzione automatica (luce, rumore, nitidezza)") if code == 0 else (None, msg[-200:])


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return p.returncode, p.stdout + p.stderr


def save_upload(name: str, data_stream: Any, length: int, target: Path | None = None) -> Path:
    """Foto o video inviati dalla pagina del telefono (iPhone): nome ripulito, mai sovrascrivere."""
    safe = "".join(c for c in Path(name).name if c.isalnum() or c in " ._-()")[:120].strip(". ") or "foto"
    if Path(safe).suffix.lower() not in PHOTO_EXT | VIDEO_EXT:
        raise ValueError("si possono inviare solo foto e video")
    base = target or resolve_folder("PICTURES") / "Telefono" / f"{datetime.now():%Y}" / f"{datetime.now():%m}"
    base.mkdir(parents=True, exist_ok=True)
    dest = base / safe
    if dest.exists() and dest.stat().st_size == length:
        return dest  # già inviata
    if dest.exists():
        dest = dest.with_name(f"{dest.stem} ({int(time.time())}){dest.suffix}")
    tmp = dest.with_name(dest.name + ".parziale")
    left = length
    with tmp.open("wb") as f:
        while left > 0:
            chunk = data_stream.read(min(1 << 20, left))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
    if left:
        tmp.unlink(missing_ok=True)
        raise ValueError("invio interrotto")
    tmp.rename(dest)
    return dest


def state_summary(sync: PhotoSync) -> str:
    rows = sync.db.execute("SELECT COUNT(*), MAX(at) FROM imported").fetchone()
    if not rows[0]:
        return "Non ho ancora salvato foto dal telefono."
    return f"Foto e video del telefono sul PC: {rows[0]}; ultimo salvataggio {time.strftime('%d/%m %H:%M', time.localtime(rows[1]))}."

