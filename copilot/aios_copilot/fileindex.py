"""Indice locale dei file dell'utente: ricerca istantanea per parole e, quando
disponibili, per significato (embedding calcolati nei momenti di riposo).

Tutto il lavoro è diviso in piccoli passi, ciascuno in una transazione SQLite:
lo spegnimento o lo standby a metà non corrompono nulla, e l'indicizzazione
riprende esattamente da dove era arrivata.
"""

from __future__ import annotations

import array
import math
import os
import re
import sqlite3
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Callable, Sequence

from .privacy import is_excluded, private_dir, redact_secrets

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".org", ".tex", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".html", ".htm", ".xml", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".sql",
}
DOC_EXTENSIONS = {".pdf", ".docx", ".odt", ".ods", ".odp", ".pptx", ".xlsx"}
MAX_FILE_BYTES = 25 * 2**20
MAX_TEXT_CHARS = 400_000
CHUNK_CHARS = 1000
RESCAN_SECONDS = 30 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, mtime REAL, size INTEGER, status TEXT, cycle INTEGER
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY, path TEXT NOT NULL, n INTEGER, text TEXT NOT NULL, vector BLOB
);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS pending_dirs (path TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS pending_files (path TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios"


# --- Estrazione del testo -----------------------------------------------------------


def _xml_text(xml: str) -> str:
    xml = re.sub(r"</(?:w:p|text:p|text:h|a:p|row)>", "\n", xml)
    xml = re.sub(r"<[^>]+>", " ", xml)
    from html import unescape

    return re.sub(r"[ \t]+", " ", unescape(xml))


def extract_text(path: Path, run: Callable[[list[str]], str] | None = None) -> str:
    """Testo leggibile di un file supportato ('' se non si riesce)."""
    ext = path.suffix.lower()
    try:
        if ext in TEXT_EXTENSIONS:
            raw = path.read_bytes()[: MAX_TEXT_CHARS * 2]
            if b"\x00" in raw[:4096]:
                return ""  # binario travestito
            text = raw.decode("utf-8", errors="replace")
            if ext in (".html", ".htm"):
                from .tools.web import html_to_text

                text = html_to_text(text, MAX_TEXT_CHARS)
            return text[:MAX_TEXT_CHARS]
        if ext == ".pdf":
            runner = run or _run_pdftotext
            return runner(["pdftotext", "-q", "-l", "60", str(path), "-"])[:MAX_TEXT_CHARS]
        if ext in DOC_EXTENSIONS:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if "content.xml" in names:  # OpenDocument
                    parts = ["content.xml"]
                elif "word/document.xml" in names:
                    parts = ["word/document.xml"]
                elif ext == ".pptx":
                    parts = sorted(n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
                else:  # .xlsx: le celle di testo stanno nelle stringhe condivise
                    parts = [n for n in names if n == "xl/sharedStrings.xml"]
                text = "\n".join(_xml_text(z.read(p).decode("utf-8", errors="replace")) for p in parts)
                return text[:MAX_TEXT_CHARS]
    except (OSError, zipfile.BadZipFile, KeyError, subprocess.SubprocessError):
        return ""
    return ""


def _run_pdftotext(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current = [], ""
    for p in paragraphs:
        while len(p) > size:  # paragrafi enormi: tagliati a misura
            if current:
                chunks.append(current)
                current = ""
            chunks.append(p[:size])
            p = p[size:]
        if len(current) + len(p) + 2 > size and current:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks


def fts_query(text: str) -> str:
    """Query FTS5 sicura: parole significative, con prefisso, in OR (BM25 premia chi ne ha di più)."""
    from .semantic import content_words

    words = [w for w in content_words(text) if len(w) > 1][:12]
    terms = []
    for w in words:
        stem = w[:-1] if len(w) > 5 else w  # "preventivi" trova anche "preventivo"
        terms.append(f'"{stem}"*')
    return " OR ".join(terms)


def _pack(vector: Sequence[float]) -> bytes:
    return array.array("f", vector).tobytes()


def _unpack(blob: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(blob)
    return a


class FileIndex:
    def __init__(
        self,
        db_path: Path | None = None,
        roots: Sequence[Path] | None = None,
        excluded: list[Path] | None = None,
        extractor: Callable[[Path], str] = extract_text,
        clock: Callable[[], float] = time.time,
    ):
        if db_path is None:
            db_path = private_dir(data_dir()) / "index.db"
        new = not db_path.exists()
        self.db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        if new:
            os.chmod(db_path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.roots = [Path(r).resolve() for r in (roots or [Path.home()])]
        self.excluded = excluded  # None = leggi la configurazione dell'utente
        self.extract = extractor
        self.clock = clock

    # --- stato -----------------------------------------------------------------
    def _get(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def _set(self, key: str, value: object) -> None:
        self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, str(value)))

    def stats(self) -> dict[str, int]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return {
            "files": q("SELECT COUNT(*) FROM files WHERE status = 'ok'"),
            "chunks": q("SELECT COUNT(*) FROM chunks"),
            "with_vectors": q("SELECT COUNT(*) FROM chunks WHERE vector IS NOT NULL"),
            "pending": q("SELECT COUNT(*) FROM pending_dirs") + q("SELECT COUNT(*) FROM pending_files"),
        }

    # --- scansione a passi -------------------------------------------------------
    def has_work(self) -> bool:
        if self.db.execute("SELECT 1 FROM pending_dirs UNION ALL SELECT 1 FROM pending_files LIMIT 1").fetchone():
            return True
        return self.clock() - float(self._get("last_scan", "0")) > RESCAN_SECONDS

    def step(self, deadline: float) -> None:
        """Lavora fino a `deadline` (tempo di self.clock) un elemento alla volta."""
        while self.clock() < deadline:
            if not self._step_one():
                return

    def _step_one(self) -> bool:
        row = self.db.execute("SELECT path FROM pending_files LIMIT 1").fetchone()
        if row:
            self._index_file(Path(row[0]))
            return True
        row = self.db.execute("SELECT path FROM pending_dirs LIMIT 1").fetchone()
        if row:
            self._visit_dir(Path(row[0]))
            return True
        if self.clock() - float(self._get("last_scan", "0")) > RESCAN_SECONDS:
            self._start_cycle()
            return True
        return False

    def _excluded(self, path: Path) -> bool:
        return is_excluded(path, self.excluded)

    def _start_cycle(self) -> None:
        with self.db:
            self.db.execute("BEGIN")
            cycle = int(self._get("cycle", "0"))
            if cycle:  # fine del giro precedente: via i file che non esistono più
                for (path,) in self.db.execute("SELECT path FROM files WHERE cycle < ?", (cycle,)).fetchall():
                    self._drop(path)
            self._set("cycle", cycle + 1)
            self._set("last_scan", self.clock())
            for root in self.roots:
                if root.is_dir() and not self._excluded(root):
                    self.db.execute("INSERT OR IGNORE INTO pending_dirs VALUES (?)", (str(root),))

    def _visit_dir(self, directory: Path) -> None:
        cycle = int(self._get("cycle", "1"))
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("DELETE FROM pending_dirs WHERE path = ?", (str(directory),))
            try:
                entries = list(os.scandir(directory))
            except OSError:
                return
            for entry in entries:
                path = Path(entry.path)
                if entry.name.startswith("."):
                    continue  # file e cartelle nascosti: impostazioni di programmi, non documenti
                if self._excluded(path):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        self.db.execute("INSERT OR IGNORE INTO pending_dirs VALUES (?)", (entry.path,))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    ext = path.suffix.lower()
                    if ext not in TEXT_EXTENSIONS and ext not in DOC_EXTENSIONS:
                        continue
                    st = entry.stat()
                except OSError:
                    continue
                if st.st_size > MAX_FILE_BYTES:
                    continue
                old = self.db.execute("SELECT mtime, size FROM files WHERE path = ?", (entry.path,)).fetchone()
                if old and old[0] == st.st_mtime and old[1] == st.st_size:
                    self.db.execute("UPDATE files SET cycle = ? WHERE path = ?", (cycle, entry.path))
                else:
                    self.db.execute("INSERT OR IGNORE INTO pending_files VALUES (?)", (entry.path,))

    def _drop(self, path: str) -> None:
        for (cid,) in self.db.execute("SELECT id FROM chunks WHERE path = ?", (path,)).fetchall():
            self.db.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
        self.db.execute("DELETE FROM chunks WHERE path = ?", (path,))
        self.db.execute("DELETE FROM files WHERE path = ?", (path,))

    def _index_file(self, path: Path) -> None:
        cycle = int(self._get("cycle", "1"))
        try:
            st = path.stat()
            text = self.extract(path) if not self._excluded(path) else ""
        except OSError:
            st, text = None, ""
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("DELETE FROM pending_files WHERE path = ?", (str(path),))
            self._drop(str(path))
            if st is None:
                return
            status = "ok"
            kept = chunk_text(redact_secrets(text))
            if not kept:
                status = "empty"
            for n, chunk in enumerate(kept):
                cur = self.db.execute("INSERT INTO chunks (path, n, text) VALUES (?, ?, ?)", (str(path), n, chunk))
                # Il nome del file è cercabile quanto il contenuto.
                self.db.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, f"{path.name}\n{chunk}"))
            self.db.execute(
                "INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?)", (str(path), st.st_mtime, st.st_size, status, cycle)
            )

    def forget_under(self, folder: Path) -> int:
        """Toglie subito dall'indice tutto ciò che sta in una cartella (es. appena esclusa)."""
        prefix = str(folder.expanduser().resolve()).rstrip("/") + "/"
        with self.db:
            self.db.execute("BEGIN")
            paths = [p for (p,) in self.db.execute("SELECT path FROM files WHERE substr(path, 1, ?) = ?", (len(prefix), prefix))]
            for path in paths:
                self._drop(path)
            self.db.execute("DELETE FROM pending_files WHERE substr(path, 1, ?) = ?", (len(prefix), prefix))
            self.db.execute("DELETE FROM pending_dirs WHERE substr(path || '/', 1, ?) = ?", (len(prefix), prefix))
        return len(paths)

    # --- embedding (nei momenti di riposo) ---------------------------------------
    def vectors_for(self, model: str) -> None:
        """Le impronte valgono solo per il modello che le ha calcolate: se cambia, si rifanno (a riposo)."""
        row = self.db.execute("SELECT value FROM state WHERE key = 'modello_significato'").fetchone()
        if row and row[0] == model:
            return
        with self.db:
            self.db.execute("BEGIN")
            if row:
                self.db.execute("UPDATE chunks SET vector = NULL")
            self.db.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('modello_significato', ?)", (model,))

    def chunks_without_vectors(self, limit: int) -> list[tuple[int, str]]:
        return self.db.execute("SELECT id, text FROM chunks WHERE vector IS NULL LIMIT ?", (limit,)).fetchall()

    def store_vectors(self, items: Sequence[tuple[int, Sequence[float]]]) -> None:
        with self.db:
            self.db.execute("BEGIN")
            for cid, vec in items:
                self.db.execute("UPDATE chunks SET vector = ? WHERE id = ?", (_pack(vec), cid))

    # --- ricerca -------------------------------------------------------------------
    def search(
        self, query: str, limit: int = 8, embed: Callable[[str], Sequence[float]] | None = None
    ) -> list[dict[str, object]]:
        """Risultati per file: parole (BM25) e, se ci sono gli embedding, significato."""
        scores: dict[str, float] = {}
        snippets: dict[str, str] = {}
        match = fts_query(query)
        if match:
            rows = self.db.execute(
                "SELECT c.path, snippet(chunks_fts, 0, '«', '»', '…', 14), bm25(chunks_fts) "
                "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
                "WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT 60",
                (match,),
            ).fetchall()
            for rank, (path, snippet, _) in enumerate(rows):
                if path not in scores:
                    scores[path] = 1.0 / (10 + rank)  # fusione per posizione (RRF)
                    snippets[path] = snippet.replace("\n", " ")
        if embed is not None:
            q = embed(query)
            qn = math.sqrt(sum(x * x for x in q)) or 1.0
            best: dict[str, tuple[float, str]] = {}
            for path, text, blob in self.db.execute("SELECT path, text, vector FROM chunks WHERE vector IS NOT NULL"):
                v = _unpack(blob)
                sim = sum(a * b for a, b in zip(q, v)) / qn
                if path not in best or sim > best[path][0]:
                    best[path] = (sim, text)
            ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:60]
            for rank, (path, (sim, text)) in enumerate(ranked):
                if sim < 0.3:
                    break
                scores[path] = scores.get(path, 0.0) + 1.0 / (10 + rank)
                snippets.setdefault(path, text[:160].replace("\n", " ") + "…")
        ordered = sorted(scores, key=scores.get, reverse=True)[:limit]
        return [{"path": p, "snippet": snippets[p]} for p in ordered if Path(p).exists() and not self._excluded(Path(p))]

    def read(self, path: Path, max_chars: int = 6000) -> str | None:
        """Testo di un file indicizzato (None se non è nell'indice o è escluso)."""
        path = path.expanduser()
        if self._excluded(path):
            return None
        rows = self.db.execute("SELECT text FROM chunks WHERE path = ? ORDER BY n", (str(path),)).fetchall()
        if not rows:
            return None
        text = "\n\n".join(r[0] for r in rows)
        return text[:max_chars] + (" […]" if len(text) > max_chars else "")
