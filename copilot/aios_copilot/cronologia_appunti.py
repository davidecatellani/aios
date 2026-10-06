"""La cronologia degli appunti (Super+V) e le emoji (Super+.), come su Windows e macOS.

- Ogni cosa copiata (testo o immagine) entra in cronologia: le ultime 60, quelle fissate restano.
  Mai le password: i gestori di password lo segnalano (x-kde-passwordManagerHint) e SoIA non le registra.
- Tutto resta sul PC (~/.local/share/aios/appunti, leggibile solo dall'utente), e si cancella da Impostazioni
  o dal pannello.
- Scegliere una voce la rimette negli appunti e la incolla nel programma che era davanti
  (Hyprland: «sendshortcut CTRL, V»).

Il registratore gira nella shell: «wl-paste --watch» avvisa a ogni copia.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

MAX_ITEMS = 60
MAX_TEXT = 200_000
SECRET_TYPES = ("x-kde-passwordManagerHint", "application/x-secret", "x-secret")
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")

Run = Callable[..., tuple[int, bytes]]


def _run(cmd: list[str], data: bytes | None = None, timeout: float = 5) -> tuple[int, bytes]:
    if not shutil.which(cmd[0]):
        return 127, b""
    try:
        p = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, b""


def folder() -> Path:
    d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "appunti"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


class History:
    def __init__(self, base: Path | None = None, run: Run = _run, clock: Callable[[], float] = time.time):
        self.base, self.run, self.clock = base, run, clock
        self.lock = threading.Lock()

    @property
    def dir(self) -> Path:
        if self.base is not None:
            self.base.mkdir(parents=True, exist_ok=True)
            return self.base
        return folder()

    def load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads((self.dir / "cronologia.json").read_text())
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        path = self.dir / "cronologia.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        keep = {i["file"] for i in items if i.get("file")}
        for f in self.dir.glob("*.png"):
            if f.name not in keep:
                f.unlink(missing_ok=True)

    def add_text(self, text: str) -> dict[str, Any] | None:
        if not text.strip() or len(text) > MAX_TEXT:
            return None
        return self._add({"tipo": "testo", "testo": text}, hashlib.sha256(text.encode()).hexdigest()[:16])

    def add_image(self, png: bytes) -> dict[str, Any] | None:
        if not png or len(png) > 25_000_000:
            return None
        key = hashlib.sha256(png).hexdigest()[:16]
        (self.dir / f"{key}.png").write_bytes(png)
        os.chmod(self.dir / f"{key}.png", 0o600)
        return self._add({"tipo": "immagine", "file": f"{key}.png"}, key)

    def _add(self, item: dict[str, Any], key: str) -> dict[str, Any]:
        with self.lock:
            items = self.load()
            old = next((i for i in items if i["id"] == key), None)
            items = [i for i in items if i["id"] != key]
            item.update(id=key, quando=int(self.clock()), fissato=bool(old and old.get("fissato")))
            items.insert(0, item)
            pinned = [i for i in items if i.get("fissato")]
            rest = [i for i in items if not i.get("fissato")][:MAX_ITEMS]
            items = sorted(pinned + rest, key=lambda i: -i["quando"])
            self._save(items)
            return item

    def pin(self, key: str, value: bool = True) -> bool:
        with self.lock:
            items = self.load()
            for i in items:
                if i["id"] == key:
                    i["fissato"] = value
                    self._save(items)
                    return True
        return False

    def remove(self, key: str) -> bool:
        with self.lock:
            items = self.load()
            rest = [i for i in items if i["id"] != key]
            self._save(rest)
            return len(rest) != len(items)

    def clear(self, keep_pinned: bool = True) -> int:
        with self.lock:
            items = self.load()
            rest = [i for i in items if keep_pinned and i.get("fissato")]
            self._save(rest)
            return len(items) - len(rest)

    def get(self, key: str) -> dict[str, Any] | None:
        return next((i for i in self.load() if i["id"] == key), None)

    # --- dagli appunti del sistema e verso ---------------------------------------------------------
    def capture(self) -> dict[str, Any] | None:
        """Legge gli appunti di adesso e li registra (chiamata a ogni copia)."""
        code, out = self.run(["wl-paste", "--list-types"])
        if code != 0:
            return None
        types = out.decode(errors="replace").split()
        if any(t in SECRET_TYPES for t in types):
            return None  # una password da un gestore di password: mai in cronologia
        image = next((t for t in IMAGE_TYPES if t in types), None)
        if image:
            code, data = self.run(["wl-paste", "--no-newline", "--type", image], timeout=10)
            if code == 0 and image != "image/png":
                data = _to_png(data)
            return self.add_image(data) if code == 0 and data else None
        if any(t.startswith("text/") or t in ("UTF8_STRING", "STRING", "TEXT") for t in types):
            code, data = self.run(["wl-paste", "--no-newline", "--type", "text"])
            if code == 0:
                return self.add_text(data.decode(errors="replace"))
        return None

    def put(self, key: str) -> bool:
        """Rimette una voce negli appunti del sistema."""
        item = self.get(key)
        if item is None:
            return False
        if item["tipo"] == "immagine":
            data = (self.dir / item["file"]).read_bytes()
            return self.run(["wl-copy", "--type", "image/png"], data)[0] == 0
        return self.run(["wl-copy", "--"], item["testo"].encode())[0] == 0

    def put_text(self, text: str) -> bool:
        return self.run(["wl-copy", "--"], text.encode())[0] == 0


def _to_png(data: bytes) -> bytes:
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        ok, buf = loader.get_pixbuf().save_to_bufferv("png", [], [])
        return bytes(buf) if ok else b""
    except Exception:
        return b""


def paste_into_active(run: Run = _run) -> None:
    """Incolla nel programma davanti (dopo che il pannello si è chiuso)."""
    time.sleep(0.15)
    run(["hyprctl", "dispatch", "sendshortcut", "CTRL, V, activewindow"])


def watch(history: History | None = None, enabled: Callable[[], bool] = lambda: True) -> None:
    """Il registratore: una riga da «wl-paste --watch» a ogni copia."""
    history = history or History()
    if not shutil.which("wl-paste"):
        return
    while True:
        try:
            proc = subprocess.Popen(["wl-paste", "--watch", "echo", "x"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            assert proc.stdout is not None
            for _ in proc.stdout:
                if enabled():
                    history.capture()
            proc.wait()
        except Exception:
            pass
        time.sleep(5)  # sessione grafica non ancora pronta, o wl-paste caduto: si riparte


def enabled() -> bool:
    try:
        conf = json.loads((Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "appunti.json").read_text())
        return bool(conf.get("attivo", True))
    except (OSError, ValueError):
        return True


def set_enabled(value: bool) -> None:
    path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "appunti.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"attivo": bool(value)}))


# --- emoji ---------------------------------------------------------------------------------------------
CATEGORIES = [
    ("Faccine", [(0x1F600, 0x1F637), (0x1F641, 0x1F644), (0x1F910, 0x1F92F), (0x1F970, 0x1F97A), (0x1FAE0, 0x1FAE8)]),
    ("Gesti e persone", [(0x1F44A, 0x1F450), (0x1F590, 0x1F590), (0x1F595, 0x1F596), (0x1F64B, 0x1F64F), (0x1F918, 0x1F91F),
                         (0x1F930, 0x1F931), (0x1F466, 0x1F469), (0x1F474, 0x1F476), (0x1F4AA, 0x1F4AA), (0x1FAF0, 0x1FAF8)]),
    ("Cuori", [(0x1F493, 0x1F49F), (0x1F5A4, 0x1F5A4), (0x1F90D, 0x1F90E), (0x1F9E1, 0x1F9E1), (0x1FA75, 0x1FA77)]),
    ("Animali e natura", [(0x1F400, 0x1F43F), (0x1F98A, 0x1F9AE), (0x1F331, 0x1F344), (0x1F490, 0x1F490), (0x1F308, 0x1F308),
                          (0x1F30A, 0x1F30A), (0x1F319, 0x1F31F)]),
    ("Cibo", [(0x1F345, 0x1F37F), (0x1F950, 0x1F96F), (0x1F9C0, 0x1F9CB)]),
    ("Attività", [(0x1F380, 0x1F393), (0x1F3A0, 0x1F3CA), (0x1F3CF, 0x1F3D3), (0x1F3F8, 0x1F3F9), (0x1F93A, 0x1F93E), (0x1F947, 0x1F94F)]),
    ("Viaggi e luoghi", [(0x1F680, 0x1F6A4), (0x1F6B2, 0x1F6B2), (0x1F3D4, 0x1F3DF), (0x1F3E0, 0x1F3F0), (0x1F5FA, 0x1F5FF)]),
    ("Oggetti", [(0x1F4A1, 0x1F4A1), (0x1F4BB, 0x1F4DA), (0x1F4DD, 0x1F4F7), (0x1F50B, 0x1F50E), (0x1F511, 0x1F514),
                 (0x1F525, 0x1F52E), (0x1F381, 0x1F381), (0x231A, 0x231B), (0x23F0, 0x23F0)]),
    ("Simboli", [(0x2705, 0x2705), (0x274C, 0x274C), (0x2728, 0x2728), (0x2B50, 0x2B50), (0x26A1, 0x26A1), (0x2753, 0x2757),
                 (0x1F4AF, 0x1F4AF), (0x1F4A2, 0x1F4A5), (0x1F4AB, 0x1F4AC), (0x1F534, 0x1F535), (0x1F7E0, 0x1F7EB), (0x2795, 0x2797),
                 (0x27A1, 0x27A1), (0x1F51D, 0x1F51D), (0x1F195, 0x1F195), (0x1F197, 0x1F197)]),
]
TEXT_STYLE = {0x1F590, 0x1F5FA, 0x1F3D4, 0x1F3D5, 0x1F3D6, 0x1F3D7, 0x1F3D8, 0x1F3D9, 0x1F3DA, 0x1F3DB, 0x1F3DC,
              0x1F3DD, 0x1F3DE, 0x1F3DF, 0x1F3CB, 0x1F3CC, 0x1F3CD, 0x1F3CE, 0x1F441, 0x1F43F, 0x1F4FD, 0x1F50A}
# parole italiane per cercare (oltre al nome inglese di Unicode)
IT_WORDS = {
    "😀": "sorriso felice", "😂": "ridere lacrime risata", "🤣": "ridere rotolare", "😊": "sorriso contento", "😍": "innamorato cuori occhi",
    "😘": "bacio", "😎": "occhiali da sole figo", "🤔": "pensare pensieroso dubbio", "😢": "triste piangere lacrima", "😭": "piangere disperato",
    "😡": "arrabbiato rabbia", "😱": "paura urlo spavento", "😴": "dormire sonno", "🤯": "testa esplosa sconvolto", "🥳": "festa compleanno",
    "😉": "occhiolino", "🙄": "occhi al cielo", "😅": "sudore imbarazzo", "🤗": "abbraccio", "🥰": "amore affetto", "😇": "angelo santo",
    "🤩": "stelle entusiasta", "😬": "imbarazzo smorfia", "🤫": "silenzio zitto", "🤮": "vomito schifo", "🥶": "freddo gelato", "🥵": "caldo",
    "👍": "pollice su ok bene mi piace", "👎": "pollice giù no", "👏": "applauso bravo", "🙏": "grazie preghiera per favore", "👋": "ciao saluto mano",
    "💪": "forza muscoli", "🤝": "stretta di mano accordo", "✌️": "pace vittoria", "🤞": "dita incrociate speriamo", "👌": "ok perfetto",
    "❤️": "cuore rosso amore", "💔": "cuore spezzato", "🔥": "fuoco top", "✨": "brillantini magia", "⭐": "stella", "🎉": "festa coriandoli auguri",
    "🎂": "torta compleanno", "🎁": "regalo pacco", "✅": "fatto spunta ok", "❌": "no croce sbagliato", "⚠️": "attenzione avviso",
    "💯": "cento perfetto", "☕": "caffè", "🍕": "pizza", "🍝": "pasta spaghetti", "🍷": "vino", "🍺": "birra", "🐶": "cane", "🐱": "gatto",
    "🌞": "sole", "🌧️": "pioggia", "❄️": "neve", "🚗": "auto macchina", "✈️": "aereo viaggio", "🏠": "casa", "💻": "computer pc", "📱": "telefono",
    "📅": "calendario data", "⏰": "sveglia", "💡": "idea lampadina", "📌": "puntina", "🔒": "lucchetto chiuso", "🚀": "razzo veloce",
    "💰": "soldi denaro", "📷": "foto macchina fotografica", "🎵": "musica nota", "⚽": "calcio pallone", "🏆": "coppa vittoria",
}


def emoji_list() -> list[dict[str, Any]]:
    out, seen = [], set()
    for cat, ranges in CATEGORIES:
        for a, b in ranges:
            for cp in range(a, b + 1):
                ch = chr(cp)
                try:
                    name = unicodedata.name(ch).lower()
                except ValueError:
                    continue
                if cp < 0x1F000 and cp not in (0x2705, 0x274C, 0x2728, 0x2B50, 0x26A1, 0x2753, 0x2754, 0x2755, 0x2757, 0x2795, 0x2796, 0x2797, 0x231A, 0x231B, 0x23F0):
                    ch += "️"
                elif cp in (0x1F590, 0x1F5A4, 0x1F5FA) or 0x1F3D4 <= cp <= 0x1F3DF or cp in (0x1F3F8, 0x1F3F9):
                    ch += "️" if cp in (0x1F590, 0x1F5FA) or 0x1F3D4 <= cp <= 0x1F3DF else ""
                if ch in seen:
                    continue
                seen.add(ch)
                out.append({"e": ch, "c": cat, "n": (IT_WORDS.get(ch) or IT_WORDS.get(ch.rstrip("️")) or "") + " " + name})
    for ch, words in IT_WORDS.items():  # quelli fuori dagli intervalli (✌️ ☕ ❤️ ⚠️ ❄️ ✈️…)
        if ch not in seen and ch.rstrip("️") not in seen:
            out.append({"e": ch, "c": "Simboli", "n": words})
    return out
