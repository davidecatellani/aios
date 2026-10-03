"""Archivio locale della posta (SQLite + ricerca FTS5, permessi 600)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from ..privacy import private_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, account TEXT, folder TEXT, uidvalidity INTEGER, uid INTEGER,
    msgid TEXT, in_reply_to TEXT, sender_name TEXT, sender TEXT, recipients TEXT, subject TEXT,
    date TEXT, snippet TEXT, body TEXT, seen INTEGER, attachments TEXT, headers TEXT,
    category TEXT, importance INTEGER, reason TEXT, notified INTEGER DEFAULT 0, archived TEXT,
    UNIQUE(account, folder, uidvalidity, uid)
);
CREATE INDEX IF NOT EXISTS messages_date ON messages(date);
CREATE INDEX IF NOT EXISTS messages_sender ON messages(sender);
CREATE VIRTUAL TABLE IF NOT EXISTS mail_fts USING fts5(subject, sender, body, tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS sync_state (account TEXT, folder TEXT, uidvalidity INTEGER, last_uid INTEGER,
                                       PRIMARY KEY(account, folder));
CREATE TABLE IF NOT EXISTS overrides (pattern TEXT PRIMARY KEY, category TEXT);
"""


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios"


@dataclass
class Mail:
    id: int
    account: str
    folder: str
    sender_name: str
    sender: str
    recipients: list[str]
    subject: str
    date: datetime
    snippet: str
    body: str
    seen: bool
    attachments: list[str]
    category: str
    importance: int
    reason: str
    msgid: str = ""
    uid: int = 0

    def line(self) -> str:
        who = self.sender_name or self.sender
        dot = "" if self.seen else "● "
        return f"[{self.id}] {dot}{self.date:%d/%m %H:%M} · {who} — {self.subject}"


COLUMNS = ("id, account, folder, sender_name, sender, recipients, subject, date, snippet, body, seen, attachments, "
           "category, importance, reason, msgid, uid")


def _row(r: tuple) -> Mail:
    return Mail(r[0], r[1], r[2], r[3] or "", r[4] or "", json.loads(r[5] or "[]"), r[6] or "(senza oggetto)",
                datetime.fromisoformat(r[7]), r[8] or "", r[9] or "", bool(r[10]), json.loads(r[11] or "[]"),
                r[12] or "altro", r[13] or 0, r[14] or "", r[15] or "", r[16] or 0)


class MailStore:
    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = private_dir(data_dir()) / "mail.db"
        new = not db_path.exists()
        self.db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        if new:
            os.chmod(db_path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    # --- sincronizzazione ----------------------------------------------------------
    def state(self, account: str, folder: str) -> tuple[int, int]:
        row = self.db.execute("SELECT uidvalidity, last_uid FROM sync_state WHERE account = ? AND folder = ?",
                              (account, folder)).fetchone()
        return row if row else (0, 0)

    def set_state(self, account: str, folder: str, uidvalidity: int, last_uid: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO sync_state VALUES (?, ?, ?, ?)", (account, folder, uidvalidity, last_uid))

    def reset_folder(self, account: str, folder: str) -> None:
        with self.db:
            self.db.execute("BEGIN")
            for (mid,) in self.db.execute("SELECT id FROM messages WHERE account = ? AND folder = ?", (account, folder)).fetchall():
                self.db.execute("DELETE FROM mail_fts WHERE rowid = ?", (mid,))
            self.db.execute("DELETE FROM messages WHERE account = ? AND folder = ?", (account, folder))

    def add(self, fields: dict[str, Any]) -> int | None:
        with self.db:
            self.db.execute("BEGIN")
            cur = self.db.execute(
                "INSERT OR IGNORE INTO messages (account, folder, uidvalidity, uid, msgid, in_reply_to, sender_name, sender, "
                "recipients, subject, date, snippet, body, seen, attachments, headers, category, importance, reason) "
                "VALUES (:account, :folder, :uidvalidity, :uid, :msgid, :in_reply_to, :sender_name, :sender, :recipients, "
                ":subject, :date, :snippet, :body, :seen, :attachments, :headers, :category, :importance, :reason)",
                {**fields, "recipients": json.dumps(fields["recipients"]), "attachments": json.dumps(fields["attachments"]),
                 "headers": json.dumps(fields["headers"]), "date": fields["date"].isoformat()},
            )
            if not cur.rowcount:
                return None
            self.db.execute("INSERT INTO mail_fts (rowid, subject, sender, body) VALUES (?, ?, ?, ?)",
                            (cur.lastrowid, fields["subject"], f"{fields['sender_name']} {fields['sender']}", fields["body"]))
            return cur.lastrowid

    # --- lettura -------------------------------------------------------------------
    def get(self, mail_id: int) -> Mail | None:
        r = self.db.execute(f"SELECT {COLUMNS} FROM messages WHERE id = ?", (mail_id,)).fetchone()
        return _row(r) if r else None

    def inbox(self, limit: int = 20, category: str | None = None, unread: bool = False) -> list[Mail]:
        sql = f"SELECT {COLUMNS} FROM messages WHERE folder NOT LIKE '%Sent%' AND folder NOT LIKE '%Inviat%'"
        args: list[Any] = []
        if category:
            sql += " AND category = ?"
            args.append(category)
        if unread:
            sql += " AND seen = 0"
        sql += " ORDER BY date DESC LIMIT ?"
        return [_row(r) for r in self.db.execute(sql, (*args, limit))]

    def search(self, query: str, limit: int = 10) -> list[Mail]:
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1][:10]
        if not words:
            return []
        match = " OR ".join(f'"{w[:-1] if len(w) > 5 else w}"*' for w in words)
        rows = self.db.execute(
            f"SELECT {', '.join('m.' + c.strip() for c in COLUMNS.split(','))} FROM mail_fts JOIN messages m ON m.id = mail_fts.rowid "
            "WHERE mail_fts MATCH ? ORDER BY bm25(mail_fts) LIMIT ?", (match, limit)).fetchall()
        return [_row(r) for r in rows]

    def counts(self) -> dict[str, tuple[int, int]]:
        """Per categoria: (totale, non lette)."""
        rows = self.db.execute("SELECT category, COUNT(*), SUM(seen = 0) FROM messages "
                               "WHERE folder NOT LIKE '%Sent%' AND folder NOT LIKE '%Inviat%' GROUP BY category")
        return {c: (n, u or 0) for c, n, u in rows}

    def to_notify(self, threshold: int) -> list[Mail]:
        rows = self.db.execute(f"SELECT {COLUMNS} FROM messages WHERE notified = 0 AND seen = 0 AND importance >= ? "
                               "ORDER BY date", (threshold,))
        return [_row(r) for r in rows]

    def mark_notified(self, ids: Iterable[int]) -> None:
        self.db.executemany("UPDATE messages SET notified = 1 WHERE id = ?", [(i,) for i in ids])

    def mark_seen(self, mail_id: int) -> None:
        self.db.execute("UPDATE messages SET seen = 1 WHERE id = ?", (mail_id,))

    def set_archived(self, mail_id: int, folder: str) -> None:
        self.db.execute("UPDATE messages SET archived = ? WHERE id = ?", (folder, mail_id))

    def to_archive(self, categories: Iterable[str], older_than: datetime) -> list[Mail]:
        cats = list(categories)
        if not cats:
            return []
        rows = self.db.execute(
            f"SELECT {COLUMNS} FROM messages WHERE archived IS NULL AND folder = 'INBOX' AND seen = 1 AND date < ? "
            f"AND category IN ({','.join('?' * len(cats))})", (older_than.isoformat(), *cats))
        return [_row(r) for r in rows]

    # --- correzioni dell'utente ----------------------------------------------------
    def set_override(self, pattern: str, category: str) -> int:
        """Categoria scelta dall'utente per un mittente (o @dominio); riclassifica subito."""
        self.db.execute("INSERT OR REPLACE INTO overrides VALUES (?, ?)", (pattern.lower(), category))
        if pattern.startswith("@"):
            cur = self.db.execute("UPDATE messages SET category = ?, reason = 'come mi hai indicato' WHERE sender LIKE ?",
                                  (category, f"%{pattern.lower()}"))
        else:
            cur = self.db.execute("UPDATE messages SET category = ?, reason = 'come mi hai indicato' WHERE sender = ?",
                                  (category, pattern.lower()))
        return cur.rowcount

    def override_for(self, sender: str) -> str | None:
        sender = sender.lower()
        row = self.db.execute("SELECT category FROM overrides WHERE pattern = ? OR pattern = ?",
                              (sender, "@" + sender.rsplit("@", 1)[-1])).fetchone()
        return row[0] if row else None

    # --- rubrica -------------------------------------------------------------------
    def sent_count(self, address: str) -> int:
        """Quante mail hai mandato a questo indirizzo (dalla posta inviata)."""
        return self.db.execute(
            "SELECT COUNT(*) FROM messages WHERE (folder LIKE '%Sent%' OR folder LIKE '%Inviat%') AND recipients LIKE ?",
            (f'%"{address.lower()}"%',)).fetchone()[0]

    def contacts(self, name: str) -> list[tuple[str, str, int]]:
        """Indirizzi che corrispondono a un nome, dai più frequenti: (nome, indirizzo, mail scambiate)."""
        q = f"%{name.lower()}%"
        rows = self.db.execute(
            "SELECT sender_name, sender, COUNT(*) FROM messages WHERE lower(sender_name) LIKE ? OR sender LIKE ? "
            "GROUP BY sender ORDER BY COUNT(*) DESC LIMIT 10", (q, q)).fetchall()
        return [(n or a, a, c + 5 * self.sent_count(a)) for n, a, c in rows]
