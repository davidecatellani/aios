"""Le versioni dei documenti: «rimetti il contratto com'era ieri», «cosa è cambiato nella tesi da stamattina?».

Ogni pochi minuti (run) si guarda quali documenti dell'utente sono cambiati (testi, Word e LibreOffice, fogli di
calcolo, presentazioni, PDF) e se ne tiene una copia in ~/.local/share/aios/versioni. Sui dischi Btrfs (Fedora)
la copia è un «reflink»: non occupa spazio finché il documento non cambia; altrove si copia davvero, e la prima
copia si fa solo per i documenti toccati di recente (gli altri da quando cambiano la prima volta).
Le versioni si sfoltiscono da sole: tutte quelle dell'ultimo giorno, una all'ora per una settimana, una al giorno
per due mesi; e c'è un tetto di spazio. Ripristinare una versione non perde niente: quella di adesso diventa a sua
volta una versione. Niente esce dal computer; si spegne da Impostazioni › Privacy.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .privacy import is_excluded, private_dir

DOCS = {".txt", ".md", ".markdown", ".rtf", ".tex", ".csv", ".tsv", ".odt", ".ods", ".odp", ".docx", ".xlsx",
        ".pptx", ".doc", ".xls", ".ppt", ".pdf", ".pages", ".numbers", ".key"}
MAX_FILE = 50 * 2**20
QUOTA = 5 * 2**30  # oltre, via le versioni più vecchie
RECENT_DAYS = 30  # senza reflink: la prima copia solo per i documenti toccati da poco
EVERY = 180  # secondi tra un giro e l'altro
BUDGET = 300 * 2**20  # byte letti al massimo per giro (il primo giro si spalma su più giri)
SCHEMA = """
CREATE TABLE IF NOT EXISTS tracked (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, hash TEXT);
CREATE TABLE IF NOT EXISTS versions (id INTEGER PRIMARY KEY, path TEXT, t REAL, hash TEXT, size INTEGER, object TEXT);
CREATE INDEX IF NOT EXISTS versions_path ON versions(path, t);
"""
_lock = threading.Lock()


def store_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "versioni"


def settings_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "versioni.json"


def enabled() -> bool:
    try:
        return bool(json.loads(settings_path().read_text()).get("attivo", True))
    except (OSError, ValueError):
        return True


def set_enabled(on: bool) -> None:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"attivo": bool(on)}))


def home() -> Path:
    return Path(os.environ.get("AIOS_CASA", Path.home())).resolve()


class Store:
    def __init__(self, root: Path | None = None, clock: Callable[[], float] = time.time):
        self.root = private_dir(root or store_dir())
        (self.root / "oggetti").mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "indice.db", check_same_thread=False, isolation_level=None)
        self.db.executescript(SCHEMA)
        self.clock = clock
        self._reflink: bool | None = None

    # --- copie -------------------------------------------------------------------------------------------
    def _object(self, digest: str, ext: str) -> Path:
        return self.root / "oggetti" / digest[:2] / f"{digest}{ext.lower()}"

    def _copy(self, src: Path, dst: Path) -> bool:
        """Copia con reflink se il disco lo permette (non occupa spazio), altrimenti copia vera."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        if self._reflink is not False:
            try:
                p = subprocess.run(["cp", "--reflink=always", "--preserve=timestamps", str(src), str(dst)],
                                   capture_output=True, timeout=120)
                if p.returncode == 0:
                    self._reflink = True
                    return True
            except (OSError, subprocess.SubprocessError):
                pass
            self._reflink = False
        shutil.copy2(src, dst)
        return False

    def can_reflink(self) -> bool:
        if self._reflink is None:
            probe, copy = self.root / ".prova", self.root / ".prova-copia"
            probe.write_bytes(b"x")
            try:
                self._copy(probe, copy)
            finally:
                probe.unlink(missing_ok=True)
                copy.unlink(missing_ok=True)
        return bool(self._reflink)

    @staticmethod
    def digest(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()

    def snapshot(self, path: Path, digest: str | None = None) -> dict[str, Any] | None:
        """Una versione del documento così com'è adesso (niente se è uguale all'ultima)."""
        path = Path(path)
        try:
            st = path.stat()
        except OSError:
            return None
        digest = digest or self.digest(path)
        last = self.db.execute("SELECT hash FROM versions WHERE path = ? ORDER BY t DESC LIMIT 1", (str(path),)).fetchone()
        self.db.execute("INSERT OR REPLACE INTO tracked VALUES (?, ?, ?, ?)", (str(path), st.st_mtime, st.st_size, digest))
        if last and last[0] == digest:
            return None
        obj = self._object(digest, path.suffix)
        if not obj.exists():
            self._copy(path, obj)
        t = st.st_mtime  # il momento in cui il documento era così
        cur = self.db.execute("INSERT INTO versions (path, t, hash, size, object) VALUES (?, ?, ?, ?, ?)",
                              (str(path), t, digest, st.st_size, str(obj.relative_to(self.root))))
        return {"id": cur.lastrowid, "t": t}

    # --- il giro -----------------------------------------------------------------------------------------
    def scan(self, roots: list[Path] | None = None, budget: int = BUDGET) -> int:
        """Guarda i documenti: quelli nuovi o cambiati diventano una versione. → quante versioni nuove."""
        made, spent = 0, 0
        now = self.clock()
        reflink = self.can_reflink()
        known = {p: (m, s) for p, m, s in self.db.execute("SELECT path, mtime, size FROM tracked")}
        for root in roots or [home()]:
            for path, st in _walk(root):
                old = known.get(str(path))
                if old and abs(old[0] - st.st_mtime) < 1e-6 and old[1] == st.st_size:
                    continue
                if old is None and not reflink and now - st.st_mtime > RECENT_DAYS * 86400:
                    # documento vecchio su un disco senza reflink: si segue da qui, senza copiarlo
                    self.db.execute("INSERT OR REPLACE INTO tracked VALUES (?, ?, ?, ?)", (str(path), st.st_mtime, st.st_size, ""))
                    continue
                if spent + st.st_size > budget and spent:
                    return made  # il resto al prossimo giro
                spent += st.st_size
                try:
                    with _lock:
                        if self.snapshot(path):
                            made += 1
                except OSError:
                    continue
        self.thin()
        return made

    def thin(self) -> int:
        """Via le versioni in più: tutte nell'ultimo giorno, una all'ora per 7 giorni, una al giorno per 60."""
        now = self.clock()
        drop = []
        for (path,) in self.db.execute("SELECT DISTINCT path FROM versions").fetchall():
            rows = self.db.execute("SELECT id, t FROM versions WHERE path = ? ORDER BY t DESC", (path,)).fetchall()
            seen: set[tuple[str, int]] = set()
            for i, (vid, t) in enumerate(rows):
                age = now - t
                if i == 0 or age < 86400:
                    continue
                if age > 90 * 86400:
                    drop.append(vid)
                    continue
                bucket = ("ora", int(t // 3600)) if age < 7 * 86400 else ("giorno", int(t // 86400))
                if bucket in seen:
                    drop.append(vid)
                seen.add(bucket)
        total = self.db.execute("SELECT COALESCE(SUM(size), 0) FROM versions").fetchone()[0]
        if total > QUOTA:  # troppo spazio: via le più vecchie (mai l'ultima di ogni documento)
            newest = {r[0] for r in self.db.execute("SELECT MAX(id) FROM versions GROUP BY path")}
            for vid, size in self.db.execute("SELECT id, size FROM versions ORDER BY t"):
                if total <= QUOTA * 0.9:
                    break
                if vid not in newest and vid not in drop:
                    drop.append(vid)
                    total -= size
        for vid in drop:
            self._drop(vid)
        return len(drop)

    def _drop(self, vid: int) -> None:
        row = self.db.execute("SELECT object FROM versions WHERE id = ?", (vid,)).fetchone()
        self.db.execute("DELETE FROM versions WHERE id = ?", (vid,))
        if row and not self.db.execute("SELECT 1 FROM versions WHERE object = ? LIMIT 1", (row[0],)).fetchone():
            (self.root / row[0]).unlink(missing_ok=True)

    # --- per l'utente --------------------------------------------------------------------------------------
    def versions(self, path: Path) -> list[dict[str, Any]]:
        return [{"id": vid, "t": t, "dimensione": size} for vid, t, size in
                self.db.execute("SELECT id, t, size FROM versions WHERE path = ? ORDER BY t DESC", (str(path),))]

    def tracked(self) -> list[str]:
        return [p for (p,) in self.db.execute("SELECT DISTINCT path FROM versions")]

    def find(self, name: str) -> Path | None:
        """«il contratto», «tesi.docx», un percorso → il documento seguito che corrisponde meglio."""
        raw = name.strip().strip("«»\"'")
        p = Path(raw).expanduser()
        if p.is_absolute() and p.exists():
            return p
        words = [w for w in re.findall(r"\w+", raw.lower()) if w not in STOP and len(w) > 1]
        if not words:
            return None
        def rank(path: str) -> tuple[float, float]:
            stem = Path(path).stem.lower().replace("_", " ").replace("-", " ")
            hits = sum(1 for w in words if w in stem or (len(w) > 4 and w[:-1] in stem))  # «contratto» ~ «contratti»
            try:
                mtime = Path(path).stat().st_mtime
            except OSError:
                return hits / len(words) - 1, 0.0  # non c'è più: solo se non c'è altro
            return hits / len(words), mtime  # a parità di parole, il più recente

        candidates = sorted(((rank(p), p) for p in self.tracked()), reverse=True)
        return Path(candidates[0][1]) if candidates and candidates[0][0][0] >= 0.5 else None

    def at(self, path: Path, when: float) -> dict[str, Any] | None:
        """La versione com'era in quel momento (l'ultima salvata prima)."""
        row = self.db.execute("SELECT id, t FROM versions WHERE path = ? AND t <= ? ORDER BY t DESC LIMIT 1",
                              (str(path), when)).fetchone()
        return {"id": row[0], "t": row[1]} if row else None

    def previous(self, path: Path) -> dict[str, Any] | None:
        """La versione prima di quella di adesso."""
        path = Path(path)
        current = self.digest(path) if path.exists() else ""
        for vid, t, digest in self.db.execute("SELECT id, t, hash FROM versions WHERE path = ? ORDER BY t DESC", (str(path),)):
            if digest != current:
                return {"id": vid, "t": t}
        return None

    def file_of(self, vid: int) -> Path | None:
        row = self.db.execute("SELECT object FROM versions WHERE id = ?", (vid,)).fetchone()
        return self.root / row[0] if row else None

    def restore(self, path: Path, vid: int) -> str:
        """Rimette la versione: quella di adesso resta tra le versioni (si può tornare indietro)."""
        path = Path(path)
        src = self.file_of(vid)
        row = self.db.execute("SELECT t FROM versions WHERE id = ? AND path = ?", (vid, str(path))).fetchone()
        if src is None or row is None or not src.exists():
            return "Non trovo quella versione."
        with _lock:
            if path.exists():
                self.snapshot(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.soia-ripristino")
            shutil.copy2(src, tmp)
            os.utime(tmp, None)  # è una modifica di adesso: i programmi aperti se ne accorgono
            tmp.replace(path)
            self.snapshot(path)
        return f"Ho rimesso «{path.name}» com'era {describe_time(row[0], datetime.fromtimestamp(self.clock()))}. La versione di prima resta tra le versioni."

    def changes(self, path: Path, vid: int, limit: int = 12) -> str:
        """Cosa è cambiato tra una versione e il documento di adesso, in breve."""
        from .fileindex import extract_text

        old_file = self.file_of(vid)
        if old_file is None or not old_file.exists() or not Path(path).exists():
            return "Non trovo le versioni da confrontare."
        a = [l.strip() for l in extract_text(old_file).splitlines() if l.strip()]
        b = [l.strip() for l in extract_text(Path(path)).splitlines() if l.strip()]
        if not a and not b:
            return "Non riesco a leggere il testo di questo documento per confrontarlo."
        added, removed = [], []
        for op in difflib.ndiff(a, b):
            if op.startswith("+ "):
                added.append(op[2:])
            elif op.startswith("- "):
                removed.append(op[2:])
        if not added and not removed:
            return "Il testo è uguale (può essere cambiata solo la formattazione)."
        out = [f"{len(added)} righe aggiunte o cambiate, {len(removed)} tolte o cambiate."]
        out += [f"+ {l[:160]}" for l in added[:limit]]
        out += [f"- {l[:160]}" for l in removed[:limit]]
        return "\n".join(out)

    def usage(self) -> dict[str, Any]:
        n, size = self.db.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM versions").fetchone()
        docs = self.db.execute("SELECT COUNT(DISTINCT path) FROM versions").fetchone()[0]
        return {"versioni": n, "documenti": docs, "dimensione": size, "reflink": bool(self._reflink)}

    def forget(self) -> None:
        self.db.execute("DELETE FROM versions")
        self.db.execute("DELETE FROM tracked")
        shutil.rmtree(self.root / "oggetti", ignore_errors=True)
        (self.root / "oggetti").mkdir(exist_ok=True)


STOP = {"il", "lo", "la", "i", "gli", "le", "un", "una", "uno", "del", "della", "dello", "dei", "delle", "di", "mio",
        "mia", "miei", "mie", "documento", "file", "foglio"}


def _walk(root: Path):
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or e.name.endswith(".soia-ripristino"):
                continue
            path = Path(e.path)
            try:
                if e.is_dir(follow_symlinks=False):
                    if not is_excluded(path):
                        stack.append(path)
                    continue
                if not e.is_file(follow_symlinks=False) or path.suffix.lower() not in DOCS or e.name.startswith("~$"):
                    continue
                st = e.stat()
            except OSError:
                continue
            if 0 < st.st_size <= MAX_FILE and not is_excluded(path):
                yield path, st


# --- i momenti a parole ------------------------------------------------------------------------------------
WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


def moment(phrase: str, now: datetime | None = None) -> float | None:
    """«ieri», «stamattina», «un'ora fa», «3 giorni fa», «lunedì», «il 3 ottobre» → l'istante a cui tornare.
    "precedente" (o vuoto) → None: si intende la versione prima di quella di adesso."""
    now = now or datetime.now()
    p = phrase.lower().strip()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    from .when import NUMBERS

    m = re.search(r"(\d+|mezz|[a-zé]+)\s*'?\s*(minut|or[ae]|giorn|settiman)", p)
    if m and "fa" in p.split() and (m.group(1).isdigit() or m.group(1) in NUMBERS or m.group(1) == "mezz"):
        g = m.group(1)
        n = int(g) if g.isdigit() else (0.5 if g == "mezz" else NUMBERS[g])
        unit = {"minut": 60, "ora": 3600, "ore": 3600, "giorn": 86400, "settiman": 7 * 86400}[m.group(2)]
        return now.timestamp() - n * unit
    if "ieri mattina" in p:
        return (midnight - timedelta(hours=11)).timestamp()
    if "ieri" in p:
        return midnight.timestamp()
    if "stamattina" in p or "questa mattina" in p or "stamani" in p:
        return min(now, midnight.replace(hour=13)).timestamp()
    if "oggi" in p:
        return midnight.timestamp()
    if "settimana scorsa" in p:
        return (now - timedelta(days=7)).timestamp()
    for i, day in enumerate(WEEKDAYS):
        if day in p or day.rstrip("ì") + "i" in p:
            back = (now.weekday() - i) % 7 or 7
            return (midnight - timedelta(days=back - 1)).timestamp()
    from .when import MONTHS

    m = re.search(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")", p)
    if m:
        try:
            d = datetime(now.year, MONTHS[m.group(2)], int(m.group(1))) + timedelta(days=1)
            if d > now:
                d = d.replace(year=now.year - 1)
            return d.timestamp()
        except ValueError:
            return None
    return None


def describe_time(t: float, now: datetime | None = None) -> str:
    now = now or datetime.now()
    d = datetime.fromtimestamp(t)
    if d.date() == now.date():
        return f"oggi alle {d:%H:%M}"
    if d.date() == (now - timedelta(days=1)).date():
        return f"ieri alle {d:%H:%M}"
    return f"il {d.day}/{d.month} alle {d:%H:%M}"


_shared: Store | None = None


def shared() -> Store:
    global _shared
    if _shared is None:
        _shared = Store()
    return _shared


def run(stop: threading.Event | None = None) -> None:
    """Il giro ogni pochi minuti, con la priorità più bassa (la shell lo avvia in un thread)."""
    stop = stop or threading.Event()
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except (OSError, AttributeError):
        pass
    stop.wait(60)  # all'avvio c'è altro da fare
    while not stop.is_set():
        if enabled():
            try:
                shared().scan()
            except Exception:
                pass
        stop.wait(EVERY)
