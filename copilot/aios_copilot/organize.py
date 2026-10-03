"""Catalogazione automatica di tutto: documenti, foto, video, musica, app, giochi, download.

Di default NON si sposta niente: i file vengono raccolti in **raccolte** (Fatture,
Contratti, Foto · Agosto 2026, Musica · Lucio Dalla, Giochi…) visibili dal copilota e
nella cartella ~/Raccolte, fatta di collegamenti ai file originali.

Il riordino vero («riordina la Scrivania») è solo su richiesta: prima un piano da
approvare, poi lo spostamento con un registro che permette di annullare tutto.
Non si toccano mai file nascosti, progetti (cartelle con .git), cartelle escluse.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Iterator

from . import metadata
from .privacy import is_excluded, private_dir
from .xdg import resolve_folder

DOC_EXT = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".xls", ".xlsx", ".ods", ".csv", ".ppt", ".pptx", ".odp", ".epub"}
PHOTO_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".webp", ".dng", ".raw", ".cr2", ".nef", ".arw"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".3gp"}
MUSIC_EXT = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aac", ".wma"}
INSTALLER_EXT = {".deb", ".rpm", ".appimage", ".flatpakref", ".exe", ".msi", ".apk", ".dmg", ".pkg"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".xz", ".bz2", ".7z", ".rar", ".zst"}
PARTIAL_EXT = {".part", ".crdownload", ".download", ".partial"}

# Argomenti dei documenti: parole nel testo (o nel nome) → raccolta.
TOPICS: list[tuple[str, re.Pattern[str]]] = [
    ("Fatture e ricevute", re.compile(r"\b(fattura|ricevuta|scontrino|nota di credito|invoice|receipt|imponibile|iva\b)", re.I)),
    ("Banca e tasse", re.compile(r"\b(estratto conto|bonifico|iban|f24|730|modello unico|imu|tari|agenzia delle entrate|cud|certificazione unica|mutuo)\b", re.I)),
    ("Contratti", re.compile(r"\b(contratto|condizioni generali|le parti|sottoscritt|clausol|recesso)\b", re.I)),
    ("Casa", re.compile(r"\b(affitto|locazione|condominio|bolletta|utenze|enel|gas|acqua potabile|rogito|planimetria|ristrutturazione)\b", re.I)),
    ("Salute", re.compile(r"\b(referto|ricetta medica|prescrizione|esami del sangue|visita (?:medica|specialistica)|asl|ospedale|vaccin|certificato medico)\b", re.I)),
    ("Auto", re.compile(r"\b(bollo auto|rc ?auto|assicurazione|revisione|libretto di circolazione|targa|carrozzeria|officina)\b", re.I)),
    ("Viaggi", re.compile(r"\b(biglietto|prenotazione|check-in|carta d'imbarco|boarding pass|volo|hotel|itinerario|trenitalia|italo)\b", re.I)),
    ("Lavoro", re.compile(r"\b(preventivo|offerta commerciale|progetto|riunione|verbale|cliente|fornitore|ordine d'acquisto|busta paga|cedolino)\b", re.I)),
    ("Scuola e studio", re.compile(r"\b(tesi|appunti|lezione|esame|università|corso di|compito|verifica|pagella|dispensa)\b", re.I)),
    ("Curriculum", re.compile(r"\b(curriculum|cv|esperienze lavorative|competenze)\b", re.I)),
    ("Manuali", re.compile(r"\b(manuale|istruzioni per l'uso|guida rapida|user manual|garanzia)\b", re.I)),
    ("Ricette", re.compile(r"\b(ricetta|ingredienti|preparazione|forno a \d+|q\.b\.)\b", re.I)),
]

APP_CATEGORIES = [
    ("Giochi", ("Game",)), ("Ufficio", ("Office",)), ("Grafica e foto", ("Graphics", "Photography")),
    ("Musica e video", ("AudioVideo", "Audio", "Video")), ("Internet", ("Network", "WebBrowser", "Email", "Chat")),
    ("Sviluppo", ("Development",)), ("Studio", ("Education", "Science")), ("Sistema", ("System", "Settings")),
    ("Accessori", ("Utility", "Accessories")),
]
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
          "novembre", "dicembre"]
EVENT_GAP = timedelta(hours=8)  # foto più distanti di così appartengono a momenti diversi


@dataclass
class Entry:
    path: str
    kind: str  # documento | foto | screenshot | video | musica | app | gioco | installer | archivio | altro
    collection: str
    group: str = ""  # sottogruppo: anno, evento, album...
    date: str = ""
    detail: dict = field(default_factory=dict)


def data_dir() -> Path:
    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios")


def default_roots(home: Path | None = None) -> list[Path]:
    return [resolve_folder(k, home) for k in ("DESKTOP", "DOWNLOAD", "DOCUMENTS", "PICTURES", "VIDEOS", "MUSIC")]


# --- classificazione ---------------------------------------------------------------------------


def _mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return datetime.now()


def classify_file(path: Path, read_text: Callable[[Path], str]) -> Entry:
    ext = path.suffix.lower()
    name = path.name.lower()
    when = _mtime(path)
    if ext in PARTIAL_EXT:
        return Entry(str(path), "altro", "Da riordinare", "scaricamenti interrotti", when.isoformat())
    if ext in DOC_EXT:
        text = read_text(path)[:20000]
        sample = f"{path.stem}\n{text}"
        topic = next((t for t, rx in TOPICS if rx.search(sample)), "Altri documenti")
        years = re.findall(r"\b(20[0-4]\d)\b", sample[:4000])
        year = max(set(years), key=years.count) if years else str(when.year)
        return Entry(str(path), "documento", topic, year, when.isoformat())
    if ext in PHOTO_EXT:
        if re.search(r"screenshot|schermata|istantanea|screen ?shot", name):
            return Entry(str(path), "screenshot", "Screenshot", f"{MONTHS[when.month - 1]} {when.year}", when.isoformat())
        taken = metadata.photo_date(path)
        cam = metadata.exif(path).get("camera", "") if ext in (".jpg", ".jpeg") else ""
        if taken is None and not cam and ext in (".png", ".webp"):
            return Entry(str(path), "foto", "Immagini", str(when.year), when.isoformat())
        taken = taken or when
        return Entry(str(path), "foto", "Foto", "", taken.isoformat(), {"camera": cam})
    if ext in VIDEO_EXT:
        if re.search(r"screencast|registrazione schermo|screen ?record", name):
            return Entry(str(path), "video", "Registrazioni dello schermo", str(when.year), when.isoformat())
        taken = metadata.photo_date(path) or when
        return Entry(str(path), "video", "Video", f"{MONTHS[taken.month - 1]} {taken.year}", taken.isoformat())
    if ext in MUSIC_EXT:
        tags = metadata.music_tags(path)
        artist = tags.get("artist") or "Artisti vari"
        return Entry(str(path), "musica", f"Musica · {artist}", tags.get("album", ""), when.isoformat(), tags)
    if ext in INSTALLER_EXT or name.endswith(".appimage"):
        system = {".exe": "Windows", ".msi": "Windows", ".apk": "Android", ".dmg": "macOS", ".pkg": "macOS"}.get(ext, "Linux")
        return Entry(str(path), "installer", "Installer", system, when.isoformat())
    if ext in ARCHIVE_EXT:
        return Entry(str(path), "archivio", "Archivi compressi", str(when.year), when.isoformat())
    return Entry(str(path), "altro", "Altro", str(when.year), when.isoformat())


def group_photo_events(entries: list[Entry]) -> None:
    """Foto vicine nel tempo = stesso momento: «Foto · 12–15 agosto 2026»."""
    photos = sorted((e for e in entries if e.kind == "foto" and e.collection == "Foto"), key=lambda e: e.date)
    event: list[Entry] = []

    def close() -> None:
        if not event:
            return
        a, b = datetime.fromisoformat(event[0].date), datetime.fromisoformat(event[-1].date)
        if a.date() == b.date():
            label = f"{a.day} {MONTHS[a.month - 1]} {a.year}"
        elif a.month == b.month:
            label = f"{a.day}–{b.day} {MONTHS[a.month - 1]} {a.year}"
        else:
            label = f"{a.day} {MONTHS[a.month - 1]} – {b.day} {MONTHS[b.month - 1]} {b.year}"
        for e in event:
            e.group = label

    for e in photos:
        if event and datetime.fromisoformat(e.date) - datetime.fromisoformat(event[-1].date) > EVENT_GAP:
            close()
            event = []
        event.append(e)
    close()


def apps(dirs: Iterable[Path] | None = None) -> list[Entry]:
    """App e giochi installati, raggruppati per uso (con origine Windows/Android)."""
    from .tools.apps import desktop_dirs

    found: dict[str, Entry] = {}
    for directory in dirs if dirs is not None else desktop_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.desktop")):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(path, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if entry.get("NoDisplay", "false").lower() == "true" or entry.get("Type", "Application") != "Application":
                continue
            name = entry.get("Name[it]") or entry.get("Name", path.stem)
            cats = entry.get("Categories", "").split(";")
            collection = next((label for label, keys in APP_CATEGORIES if any(k in cats for k in keys)), "Altre app")
            exec_line = entry.get("Exec", "").lower()
            origin = "Windows" if re.search(r"bottles|wine", exec_line) else "Android" if "waydroid" in exec_line else ""
            kind = "gioco" if collection == "Giochi" else "app"
            found.setdefault(name, Entry(str(path), kind, collection, origin, detail={"name": name}))
    return list(found.values())


# --- scansione e archivio -----------------------------------------------------------------------


def walk(roots: Iterable[Path], excluded: list[Path] | None = None, max_depth: int = 6) -> Iterator[Path]:
    for root in roots:
        if not root.is_dir() or is_excluded(root, excluded):
            continue
        stack = [(root, 0)]
        while stack:
            directory, depth = stack.pop()
            try:
                children = sorted(os.scandir(directory), key=lambda e: e.name)
            except OSError:
                continue
            if any(c.name == ".git" for c in children):
                continue  # progetto di sviluppo: si lascia com'è
            for child in children:
                path = Path(child.path)
                if child.name.startswith(".") or is_excluded(path, excluded):
                    continue
                if child.is_dir(follow_symlinks=False):
                    if depth < max_depth and path.name != "Raccolte":
                        stack.append((path, depth + 1))
                elif child.is_file(follow_symlinks=False):
                    yield path


class Library:
    """Le raccolte di tutti i file dell'utente, aggiornate per differenza."""

    def __init__(self, db_path: Path | None = None, roots: list[Path] | None = None,
                 read_text: Callable[[Path], str] | None = None, excluded: list[Path] | None = None,
                 app_dirs: Iterable[Path] | None = None):
        db_path = db_path or data_dir() / "library.db"
        new = not db_path.exists()
        self.db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        if new:
            os.chmod(db_path, 0o600)
        self.db.execute("CREATE TABLE IF NOT EXISTS items (path TEXT PRIMARY KEY, mtime REAL, kind TEXT, collection TEXT,"
                        " grp TEXT, date TEXT, detail TEXT)")
        self.roots = roots if roots is not None else default_roots()
        self.excluded = excluded
        self.app_dirs = app_dirs
        if read_text is None:
            from .fileindex import extract_text

            read_text = extract_text
        self.read_text = read_text

    def refresh(self, deadline: float | None = None) -> bool:
        """Aggiorna le raccolte; con `deadline` (time.monotonic) si ferma e riprende dopo. True se finito."""
        known = dict(self.db.execute("SELECT path, mtime FROM items WHERE kind NOT IN ('app', 'gioco')"))
        seen = set()
        with self.db:
            self.db.execute("BEGIN")
            for path in walk(self.roots, self.excluded):
                seen.add(str(path))
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if known.get(str(path)) == mtime:
                    continue
                e = classify_file(path, self.read_text)
                self.db.execute("INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (e.path, mtime, e.kind, e.collection, e.group, e.date, json.dumps(e.detail)))
                if deadline is not None and time.monotonic() > deadline:
                    return False  # la transazione si chiude: il lavoro fatto resta
            for path in set(known) - seen:
                self.db.execute("DELETE FROM items WHERE path = ?", (path,))
            self.db.execute("DELETE FROM items WHERE kind IN ('app', 'gioco')")
            for e in apps(self.app_dirs):
                self.db.execute("INSERT OR REPLACE INTO items VALUES (?, 0, ?, ?, ?, '', ?)",
                                (e.path, e.kind, e.collection, e.group, json.dumps(e.detail)))
        self._group_events()
        return True

    def _group_events(self) -> None:
        rows = self.db.execute("SELECT path, date FROM items WHERE kind = 'foto' AND collection = 'Foto'").fetchall()
        entries = [Entry(p, "foto", "Foto", "", d) for p, d in rows]
        group_photo_events(entries)
        with self.db:
            self.db.execute("BEGIN")
            self.db.executemany("UPDATE items SET grp = ? WHERE path = ?", [(e.group, e.path) for e in entries])

    def entries(self, collection: str | None = None, kind: str | None = None) -> list[Entry]:
        sql, args = "SELECT path, kind, collection, grp, date, detail FROM items WHERE 1=1", []
        if collection:
            sql += " AND collection = ?"
            args.append(collection)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        sql += " ORDER BY date DESC, path"
        return [Entry(p, k, c, g, d, json.loads(det or "{}")) for p, k, c, g, d, det in self.db.execute(sql, args)]

    def collections(self) -> list[tuple[str, int]]:
        return list(self.db.execute("SELECT collection, COUNT(*) FROM items GROUP BY collection ORDER BY COUNT(*) DESC"))

    def duplicates(self, kinds: tuple[str, ...] = ("foto", "musica", "video", "documento", "installer")) -> list[list[str]]:
        """File identici (stessa dimensione e stesso contenuto)."""
        by_size: dict[int, list[str]] = {}
        for (path,) in self.db.execute(f"SELECT path FROM items WHERE kind IN ({','.join('?' * len(kinds))})", kinds):
            try:
                by_size.setdefault(os.path.getsize(path), []).append(path)
            except OSError:
                continue
        groups = []
        for size, paths in by_size.items():
            if len(paths) < 2 or size == 0:
                continue
            by_hash: dict[str, list[str]] = {}
            for p in paths:
                try:
                    by_hash.setdefault(hashlib.sha256(Path(p).read_bytes()).hexdigest(), []).append(p)
                except OSError:
                    continue
            groups += [sorted(g) for g in by_hash.values() if len(g) > 1]
        return groups

    def cleanup_suggestions(self, now: datetime | None = None) -> list[tuple[str, str]]:
        """Cose da riordinare: (file, motivo). Solo proposte."""
        now = now or datetime.now()
        out = []
        for e in self.entries():
            age = now - datetime.fromisoformat(e.date) if e.date else timedelta()
            if e.collection == "Da riordinare":
                out.append((e.path, "scaricamento interrotto"))
            elif e.kind == "installer" and age > timedelta(days=30):
                out.append((e.path, f"installer {e.group} di oltre un mese fa"))
            elif e.kind == "archivio" and Path(e.path).with_suffix("").is_dir():
                out.append((e.path, "archivio già estratto"))
        for group in self.duplicates():
            out += [(p, f"copia identica di {Path(group[0]).name}") for p in group[1:]]
        return out

    # --- vista nel file manager --------------------------------------------------------------
    def build_view(self, target: Path | None = None) -> Path:
        """~/Raccolte: cartelle di collegamenti ai file originali (nessuna copia, nessuno spostamento)."""
        target = target or Path.home() / "Raccolte"
        if target.exists():
            marker = target / ".aios-raccolte"
            if not marker.exists():
                raise RuntimeError(f"{target} esiste già e non è stata creata da AIOS: non la tocco.")
            shutil.rmtree(target)
        target.mkdir(parents=True)
        (target / ".aios-raccolte").write_text("Cartella generata da AIOS: contiene solo collegamenti ai tuoi file.\n")
        for e in self.entries():
            if e.kind in ("app", "gioco"):
                continue
            folder = target / _safe(e.collection)
            if e.group:
                folder = folder / _safe(e.group)
            folder.mkdir(parents=True, exist_ok=True)
            link = folder / Path(e.path).name
            n = 2
            while link.exists() or link.is_symlink():
                link = folder / f"{Path(e.path).stem} ({n}){Path(e.path).suffix}"
                n += 1
            link.symlink_to(e.path)
        return target


def _safe(name: str) -> str:
    return re.sub(r"[/\\:\0]", "-", name).strip() or "Senza nome"


# --- riordino vero, con anteprima e annullamento ------------------------------------------------


def plan_tidy(library: Library, folder: Path) -> list[tuple[str, str]]:
    """Dove andrebbe ogni file lasciato «sparso» in una cartella (es. Scrivania, Download)."""
    moves = []
    targets = {
        "documento": lambda e: resolve_folder("DOCUMENTS") / e.collection / e.group,
        "foto": lambda e: resolve_folder("PICTURES") / (e.group or "Foto"),
        "screenshot": lambda e: resolve_folder("PICTURES") / "Screenshot",
        "video": lambda e: resolve_folder("VIDEOS") / (e.group or "Video"),
        "musica": lambda e: resolve_folder("MUSIC") / e.collection.removeprefix("Musica · ") / (e.group or ""),
        "installer": lambda e: resolve_folder("DOWNLOAD") / "Installer",
        "archivio": lambda e: resolve_folder("DOWNLOAD") / "Archivi",
    }
    folder = folder.resolve()
    for e in library.entries():
        path = Path(e.path)
        if path.parent.resolve() != folder or e.kind not in targets:
            continue  # solo i file sparsi in quella cartella, non quelli già nelle sottocartelle
        dest_dir = targets[e.kind](e)
        if dest_dir.resolve() == folder:
            continue
        dest = dest_dir / path.name
        n = 2
        while dest.exists():
            dest = dest_dir / f"{path.stem} ({n}){path.suffix}"
            n += 1
        moves.append((str(path), str(dest)))
    return moves


def journal_path() -> Path:
    return data_dir() / "riordino.json"


def apply_tidy(moves: list[tuple[str, str]]) -> int:
    """Esegue gli spostamenti e li registra: annullabili con undo_tidy()."""
    done = []
    for src, dest in moves:
        s, d = Path(src), Path(dest)
        if not s.exists() or d.exists():
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        done.append((src, dest))
    history = _journal()
    history.append({"time": datetime.now().isoformat(timespec="seconds"), "moves": done})
    journal_path().write_text(json.dumps(history, ensure_ascii=False, indent=1))
    return len(done)


def undo_tidy() -> int:
    """Annulla l'ultimo riordino: ogni file torna dov'era."""
    history = _journal()
    if not history:
        return 0
    last = history.pop()
    restored = 0
    for src, dest in reversed(last["moves"]):
        s, d = Path(src), Path(dest)
        if d.exists() and not s.exists():
            s.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d), str(s))
            restored += 1
            try:
                d.parent.rmdir()  # cartelle rimaste vuote: via
            except OSError:
                pass
    journal_path().write_text(json.dumps(history, ensure_ascii=False, indent=1))
    return restored


def _journal() -> list[dict]:
    try:
        return json.loads(journal_path().read_text())
    except (OSError, ValueError):
        return []
