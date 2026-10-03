"""Agenda e promemoria locali, con notifiche e riepilogo del mattino.

Tutto resta sul dispositivo (SQLite, permessi 600). Il formato di scambio è
iCalendar (.ics), così l'agenda si importa ed esporta verso qualsiasi calendario.

    aios-agenda                servizio: notifiche e riepilogo del mattino
    aios-agenda oggi|domani|settimana
    aios-agenda riepilogo
    aios-agenda esporta agenda.ics | importa calendario.ics
"""

from __future__ import annotations

import argparse
import calendar
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .privacy import private_dir
from .when import describe

REPEATS = ("", "daily", "weekly", "monthly", "yearly")
BRIEFING_HOUR = 8  # riepilogo del mattino: dalle 8 (o alla prima accensione dopo)
BRIEFING_LATEST = 12  # dopo mezzogiorno non ha più senso "buongiorno"
MISSED_GRACE = timedelta(hours=6)  # avvisi persi (PC spento/standby): recuperati se recenti

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, start TEXT NOT NULL, end TEXT,
    all_day INTEGER DEFAULT 0, location TEXT DEFAULT '', notes TEXT DEFAULT '',
    repeat TEXT DEFAULT '', source TEXT DEFAULT 'user', created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, due TEXT, repeat TEXT DEFAULT '',
    done INTEGER DEFAULT 0, source TEXT DEFAULT 'user', created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, due TEXT NOT NULL, source TEXT NOT NULL,
    status TEXT DEFAULT 'pending', UNIQUE(source, due)
);
CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY, at TEXT);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios"


@dataclass
class Item:
    kind: str  # "event" | "reminder"
    id: int
    title: str
    at: datetime | None  # inizio (evento) o scadenza (promemoria); per le ricorrenze: questa occorrenza
    all_day: bool = False
    location: str = ""
    repeat: str = ""
    done: bool = False

    def line(self, now: datetime | None = None) -> str:
        when = describe(self.at, self.all_day, now) if self.at else "da fare"
        icon = "📅" if self.kind == "event" else ("✅" if self.done else "🔔")
        where = f" — {self.location}" if self.location else ""
        again = {"daily": " (ogni giorno)", "weekly": " (ogni settimana)", "monthly": " (ogni mese)",
                 "yearly": " (ogni anno)"}.get(self.repeat, "")
        return f"{icon} {when}: {self.title}{where}{again}"


def _add_months(d: date, months: int) -> date | None:
    month0 = d.month - 1 + months
    year, month = d.year + month0 // 12, month0 % 12 + 1
    if d.day > calendar.monthrange(year, month)[1]:
        return None  # "ogni 31": i mesi più corti si saltano
    return d.replace(year=year, month=month)


def occurrences(first: datetime, repeat: str, start: datetime, end: datetime) -> Iterator[datetime]:
    """Le occorrenze di un elemento (eventualmente ricorrente) comprese in [start, end)."""
    if not repeat:
        if start <= first < end:
            yield first
        return
    if repeat == "daily" and first < start:
        first += timedelta(days=(start - first).days)
    elif repeat == "weekly" and first < start:
        first += timedelta(weeks=(start - first).days // 7)
    n = 0
    current = first
    while current < end and n < 2000:
        if current >= start:
            yield current
        n += 1
        if repeat == "daily":
            current += timedelta(days=1)
        elif repeat == "weekly":
            current += timedelta(weeks=1)
        else:
            step = n if repeat == "monthly" else 12 * n
            nxt = None
            while nxt is None and step < 12 * 400:
                nxt = _add_months(first.date(), step)
                if nxt is None:
                    n += 1
                    step = n if repeat == "monthly" else 12 * n
            if nxt is None:
                return
            current = datetime.combine(nxt, first.time())


class Agenda:
    def __init__(self, db_path: Path | None = None, clock: Callable[[], datetime] = datetime.now):
        if db_path is None:
            db_path = private_dir(data_dir()) / "agenda.db"
        new = not db_path.exists()
        self.db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        if new:
            os.chmod(db_path, 0o600)
        self.db.executescript(SCHEMA)
        self._add_uids()
        self.clock = clock

    def _add_uids(self) -> None:
        """Identificativo stabile di ogni voce, uguale su tutti i dispositivi (sincronizzazione)."""
        for table in ("events", "reminders"):
            columns = [r[1] for r in self.db.execute(f"PRAGMA table_info({table})")]
            if "uid" not in columns:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN uid TEXT")
            for (rowid,) in self.db.execute(f"SELECT id FROM {table} WHERE uid IS NULL").fetchall():
                self.db.execute(f"UPDATE {table} SET uid = ? WHERE id = ?", (secrets.token_hex(8), rowid))

    def now(self) -> datetime:
        return self.clock().replace(second=0, microsecond=0)

    # --- scrittura ------------------------------------------------------------------
    def add_event(self, title: str, start: datetime, end: datetime | None = None, all_day: bool = False,
                  location: str = "", notes: str = "", repeat: str = "", source: str = "user") -> int:
        assert repeat in REPEATS
        cur = self.db.execute(
            "INSERT INTO events (title, start, end, all_day, location, notes, repeat, source, created, uid) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, start.isoformat(), end.isoformat() if end else None, int(all_day), location, notes,
             repeat, source, self.now().isoformat(), secrets.token_hex(8)),
        )
        self._skip_past_alerts("event", cur.lastrowid)
        return cur.lastrowid

    def add_reminder(self, title: str, due: datetime | None, repeat: str = "", source: str = "user") -> int:
        assert repeat in REPEATS
        cur = self.db.execute(
            "INSERT INTO reminders (title, due, repeat, source, created, uid) VALUES (?, ?, ?, ?, ?, ?)",
            (title, due.isoformat() if due else None, repeat, source, self.now().isoformat(), secrets.token_hex(8)),
        )
        self._skip_past_alerts("reminder", cur.lastrowid)
        return cur.lastrowid

    def _skip_past_alerts(self, kind: str, item_id: int) -> None:
        """Un elemento appena creato non deve far scattare avvisi già passati
        (es. «un giorno prima» di un appuntamento fissato per domattina)."""
        now = self.now()
        for item in self.between(now - MISSED_GRACE, now + timedelta(days=2)):
            if item.kind == kind and item.id == item_id:
                for key, when, _ in self._alerts(item, now):
                    if when < now:
                        self.mark_sent(key)

    def complete(self, reminder_id: int) -> bool:
        return self.db.execute("UPDATE reminders SET done = 1 WHERE id = ?", (reminder_id,)).rowcount > 0

    def delete(self, kind: str, item_id: int) -> bool:
        table = {"event": "events", "reminder": "reminders"}[kind]
        return self.db.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,)).rowcount > 0

    # --- sincronizzazione tra i dispositivi (sync.py) -----------------------------------
    EVENT_FIELDS = ("title", "start", "end", "all_day", "location", "notes", "repeat", "source")
    REMINDER_FIELDS = ("title", "due", "repeat", "done", "source")

    def sync_records(self) -> dict[str, dict]:
        records = {}
        for kind, table, fields in (("event", "events", self.EVENT_FIELDS), ("reminder", "reminders", self.REMINDER_FIELDS)):
            for row in self.db.execute(f"SELECT uid, {', '.join(fields)} FROM {table}"):
                records[row[0]] = {"kind": kind, **dict(zip(fields, row[1:]))}
        return records

    def apply_record(self, uid: str, record: dict | None) -> None:
        """Una voce arrivata da un altro dispositivo (None = cancellata là)."""
        if record is None:
            for table in ("events", "reminders"):
                self.db.execute(f"DELETE FROM {table} WHERE uid = ?", (uid,))
            return
        table, fields = (("events", self.EVENT_FIELDS) if record.get("kind") == "event" else ("reminders", self.REMINDER_FIELDS))
        values = [record.get(f) for f in fields]
        if self.db.execute(f"SELECT 1 FROM {table} WHERE uid = ?", (uid,)).fetchone():
            self.db.execute(f"UPDATE {table} SET {', '.join(f + ' = ?' for f in fields)} WHERE uid = ?", (*values, uid))
            return
        cur = self.db.execute(f"INSERT INTO {table} ({', '.join(fields)}, created, uid) VALUES ({', '.join('?' * len(fields))}, ?, ?)",
                              (*values, self.now().isoformat(), uid))
        self._skip_past_alerts(record["kind"], cur.lastrowid)  # niente avvisi già passati anche qui

    # --- lettura --------------------------------------------------------------------
    def _events(self) -> Iterable[tuple]:
        return self.db.execute("SELECT id, title, start, all_day, location, repeat FROM events")

    def _reminders(self, done: bool = False) -> Iterable[tuple]:
        return self.db.execute("SELECT id, title, due, repeat, done FROM reminders WHERE done = ?", (int(done),))

    def between(self, start: datetime, end: datetime) -> list[Item]:
        items = []
        for eid, title, s, all_day, location, repeat in self._events():
            for at in occurrences(datetime.fromisoformat(s), repeat, start, end):
                items.append(Item("event", eid, title, at, bool(all_day), location, repeat))
        for rid, title, due, repeat, _ in self._reminders():
            if due:
                for at in occurrences(datetime.fromisoformat(due), repeat, start, end):
                    items.append(Item("reminder", rid, title, at, False, "", repeat))
        return sorted(items, key=lambda i: (i.at, i.kind))

    def day(self, d: date) -> list[Item]:
        start = datetime.combine(d, time())
        return self.between(start, start + timedelta(days=1))

    def todos(self) -> list[Item]:
        return [Item("reminder", rid, t, None) for rid, t, due, _, _ in self._reminders() if not due]

    def overdue(self) -> list[Item]:
        now = self.now()
        return [Item("reminder", rid, t, datetime.fromisoformat(due))
                for rid, t, due, repeat, _ in self._reminders()
                if due and not repeat and datetime.fromisoformat(due) < now]

    def find(self, query: str) -> list[Item]:
        """Elementi il cui titolo contiene tutte le parole cercate."""
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
        found = []
        for eid, title, s, all_day, location, repeat in self._events():
            if all(w in title.lower() for w in words):
                found.append(Item("event", eid, title, datetime.fromisoformat(s), bool(all_day), location, repeat))
        for rid, title, due, repeat, _ in self._reminders():
            if all(w in title.lower() for w in words):
                found.append(Item("reminder", rid, title, datetime.fromisoformat(due) if due else None, repeat=repeat))
        return found

    # --- notifiche ------------------------------------------------------------------
    def due_notifications(self) -> list[tuple[str, str, str]]:
        """Avvisi da mostrare adesso: (chiave, titolo, testo). Una chiave non viene mai ripetuta."""
        now = self.now()
        window_start, window_end = now - MISSED_GRACE, now + timedelta(days=2)
        out = []
        for item in self.between(window_start, window_end):
            for key, when, text in self._alerts(item, now):
                if window_start <= when <= now and not self._sent(key):
                    out.append((key, item.title, text))
        return out

    def _alerts(self, item: Item, now: datetime) -> list[tuple[str, datetime, str]]:
        base = f"{item.kind}:{item.id}:{item.at.isoformat()}"
        if item.kind == "reminder":
            return [(f"{base}:0", item.at, f"🔔 {item.title}")]
        if item.all_day:
            day_before = datetime.combine(item.at.date() - timedelta(days=1), time(18))
            morning = datetime.combine(item.at.date(), time(BRIEFING_HOUR))
            return [(f"{base}:eve", day_before, f"Domani: {item.title}"),
                    (f"{base}:day", morning, f"Oggi: {item.title}")]
        where = f" — {item.location}" if item.location else ""
        return [(f"{base}:-1d", item.at - timedelta(days=1), f"Domani alle {item.at:%H:%M}: {item.title}{where}"),
                (f"{base}:-1h", item.at - timedelta(hours=1), f"Tra un'ora, alle {item.at:%H:%M}: {item.title}{where}")]

    def _sent(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM sent WHERE key = ?", (key,)).fetchone() is not None

    def mark_sent(self, key: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO sent VALUES (?, ?)", (key, self.now().isoformat()))

    # --- proposte trovate nei documenti ----------------------------------------------
    def suggest(self, title: str, due: datetime, source: str) -> int | None:
        cur = self.db.execute("INSERT OR IGNORE INTO suggestions (title, due, source) VALUES (?, ?, ?)",
                              (title, due.isoformat(), source))
        return cur.lastrowid if cur.rowcount else None

    def pending_suggestions(self) -> list[tuple[int, str, datetime, str]]:
        now = self.now()
        rows = self.db.execute("SELECT id, title, due, source FROM suggestions WHERE status = 'pending' ORDER BY due")
        return [(i, t, datetime.fromisoformat(d), s) for i, t, d, s in rows if datetime.fromisoformat(d) >= now]

    def resolve_suggestion(self, sid: int, accept: bool) -> Item | None:
        row = self.db.execute("SELECT title, due, source FROM suggestions WHERE id = ? AND status = 'pending'",
                              (sid,)).fetchone()
        if row is None:
            return None
        self.db.execute("UPDATE suggestions SET status = ? WHERE id = ?", ("accepted" if accept else "dismissed", sid))
        if not accept:
            return Item("reminder", 0, row[0], None)
        due = datetime.fromisoformat(row[1])
        rid = self.add_reminder(row[0], due, source=row[2])
        return Item("reminder", rid, row[0], due)

    # --- riepilogo ------------------------------------------------------------------
    def briefing(self, name: str = "", recent_files: Iterable[str] = ()) -> str:
        now = self.now()
        greet = "Buongiorno" if now.hour < 13 else ("Buon pomeriggio" if now.hour < 18 else "Buonasera")
        days = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
        months = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
                  "settembre", "ottobre", "novembre", "dicembre"]
        lines = [f"{greet}{', ' + name if name else ''}! Oggi è {days[now.weekday()]} {now.day} {months[now.month - 1]}."]
        today = [i for i in self.day(now.date()) if i.all_day or i.at >= now - timedelta(hours=1)]
        if today:
            lines.append(f"Oggi hai {len(today)} {'impegno' if len(today) == 1 else 'impegni'}:")
            lines += [f"  {i.line(now)}" for i in today]
        else:
            lines.append("Oggi non hai impegni in agenda.")
        overdue = self.overdue()
        if overdue:
            lines.append("Rimasti indietro:")
            lines += [f"  {i.line(now)}" for i in overdue[:5]]
        todos = self.todos()
        if todos:
            lines.append(f"Da fare, senza scadenza: {', '.join(i.title for i in todos[:5])}"
                         + (f" e altri {len(todos) - 5}" if len(todos) > 5 else "") + ".")
        tomorrow = self.day(now.date() + timedelta(days=1))
        if tomorrow:
            lines.append("Domani: " + "; ".join(i.line(now).split(": ", 1)[-1] if i.all_day else
                                               f"{i.at:%H:%M} {i.title}" for i in tomorrow[:4]) + ".")
        suggestions = self.pending_suggestions()
        if suggestions:
            lines.append("Ho trovato nei tuoi documenti delle possibili scadenze:")
            lines += [f"  [{sid}] {describe(due, due.time() == time(9), now)}: {title} ({Path(src).name})"
                      for sid, title, due, src in suggestions[:3]]
            lines.append("  Dimmi «aggiungi la scadenza N» oppure «ignora la scadenza N».")
        recent = list(recent_files)[:3]
        if recent:
            lines.append("Ultimi file su cui hai lavorato: " + ", ".join(Path(p).name for p in recent) + ".")
        return "\n".join(lines)

    def briefing_due(self) -> bool:
        """Il riepilogo del mattino va mostrato ora? (una volta al giorno, tra le 8 e mezzogiorno)"""
        now = self.now()
        if not BRIEFING_HOUR <= now.hour < BRIEFING_LATEST:
            return False
        row = self.db.execute("SELECT value FROM state WHERE key = 'briefing'").fetchone()
        return not row or row[0] != now.date().isoformat()

    def mark_briefing(self) -> None:
        self.db.execute("INSERT OR REPLACE INTO state VALUES ('briefing', ?)", (self.now().date().isoformat(),))

    # --- iCalendar ------------------------------------------------------------------
    def export_ics(self) -> str:
        freq = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY"}

        def esc(t: str) -> str:
            return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

        out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AIOS//Copilota//IT"]
        for eid, title, s, all_day, location, repeat in self._events():
            start = datetime.fromisoformat(s)
            out += ["BEGIN:VEVENT", f"UID:aios-event-{eid}",
                    f"DTSTART;VALUE=DATE:{start:%Y%m%d}" if all_day else f"DTSTART:{start:%Y%m%dT%H%M%S}",
                    f"SUMMARY:{esc(title)}"]
            if location:
                out.append(f"LOCATION:{esc(location)}")
            if repeat:
                out.append(f"RRULE:FREQ={freq[repeat]}")
            out.append("END:VEVENT")
        for rid, title, due, repeat, _ in self._reminders():
            out += ["BEGIN:VTODO", f"UID:aios-reminder-{rid}", f"SUMMARY:{esc(title)}"]
            if due:
                out.append(f"DUE:{datetime.fromisoformat(due):%Y%m%dT%H%M%S}")
            if repeat:
                out.append(f"RRULE:FREQ={freq[repeat]}")
            out.append("END:VTODO")
        out.append("END:VCALENDAR")
        return "\r\n".join(out) + "\r\n"

    def import_ics(self, text: str) -> int:
        text = re.sub(r"\r?\n[ \t]", "", text)  # righe spezzate
        count = 0
        for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
            fields = {}
            for line in block.strip().splitlines():
                key, _, value = line.partition(":")
                fields[key.split(";")[0].upper()] = (key, value.strip())
            if "DTSTART" not in fields or "SUMMARY" not in fields:
                continue
            raw = fields["DTSTART"][1]
            all_day = len(raw) == 8
            start = datetime.strptime(raw[:15], "%Y%m%d") if all_day else datetime.strptime(raw[:15], "%Y%m%dT%H%M%S")
            if raw.endswith("Z"):  # UTC → ora locale
                start = start.replace(tzinfo=__import__("datetime").timezone.utc).astimezone().replace(tzinfo=None)
            repeat = ""
            rule = fields.get("RRULE", ("", ""))[1]
            m = re.search(r"FREQ=(\w+)", rule)
            if m:
                repeat = {"DAILY": "daily", "WEEKLY": "weekly", "MONTHLY": "monthly", "YEARLY": "yearly"}.get(m.group(1), "")
            unesc = lambda t: t.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
            self.add_event(unesc(fields["SUMMARY"][1]), start, all_day=all_day,
                           location=unesc(fields.get("LOCATION", ("", ""))[1]), repeat=repeat, source="ics")
            count += 1
        return count


# --- Proposte dai documenti --------------------------------------------------------

DEADLINE_WORDS = re.compile(
    r"\b(scadenza|scade|scadono|entro il|entro e non oltre|da pagare|pagamento|appuntamento|udienza|"
    r"visita|consegna|riunione|convocazione|prenotazione|due date|deadline|appointment)\b",
    re.I,
)


def find_deadlines(text: str, now: datetime) -> list[tuple[str, datetime]]:
    """Frasi con una parola-chiave di scadenza e una data esplicita futura (entro un anno)."""
    from .when import RE_DATE, parse_when

    found = []
    for sentence in re.split(r"(?<=[.!?\n])\s+", text):
        if len(sentence) > 300 or not DEADLINE_WORDS.search(sentence) or not RE_DATE.search(sentence.lower()):
            continue
        w = parse_when(sentence, now)
        if w.at and now < w.at < now + timedelta(days=366):
            found.append((re.sub(r"\s+", " ", sentence).strip()[:120], w.at))
    return found


# --- Notifiche desktop e servizio ----------------------------------------------------


def notify(title: str, body: str, run: Callable[[list[str]], object] = subprocess.run) -> None:
    try:
        run(["notify-send", "--app-name=Copilota", "--icon=appointment-soon", title, body])
    except (FileNotFoundError, OSError):
        print(f"[{title}] {body}", flush=True)


def recent_files(limit: int = 3) -> list[str]:
    """I file modificati di recente secondo l'indice (se esiste)."""
    db = data_dir() / "index.db"
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        since = _time.time() - 3 * 86400
        rows = con.execute("SELECT path FROM files WHERE status = 'ok' AND mtime > ? ORDER BY mtime DESC LIMIT ?",
                           (since, limit)).fetchall()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []


def run_service(agenda: Agenda, notify_fn: Callable[[str, str], None] = notify,
                sleep: Callable[[float], None] = _time.sleep, once: bool = False) -> None:
    from .welcome import load_profile

    while True:
        for key, title, text in agenda.due_notifications():
            notify_fn("Copilota", text)
            agenda.mark_sent(key)
        if agenda.briefing_due():
            notify_fn("Il tuo riepilogo", agenda.briefing(load_profile().get("name", ""), recent_files()))
            agenda.mark_briefing()
        if once:
            return
        sleep(20)  # dopo lo standby il primo giro recupera gli avvisi persi (entro 6 ore)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aios-agenda", description="Agenda e promemoria di AIOS")
    parser.add_argument("comando", nargs="?", default="servizio",
                        choices=["servizio", "oggi", "domani", "settimana", "riepilogo", "esporta", "importa"])
    parser.add_argument("file", nargs="?")
    args = parser.parse_args(argv)
    agenda = Agenda()
    now = agenda.now()
    if args.comando == "servizio":
        try:
            run_service(agenda)
        except KeyboardInterrupt:
            pass
    elif args.comando in ("oggi", "domani"):
        d = now.date() + timedelta(days=1 if args.comando == "domani" else 0)
        items = agenda.day(d)
        print("\n".join(i.line(now) for i in items) or "Nessun impegno.")
    elif args.comando == "settimana":
        items = agenda.between(datetime.combine(now.date(), time()), datetime.combine(now.date(), time()) + timedelta(days=7))
        print("\n".join(i.line(now) for i in items) or "Nessun impegno nei prossimi 7 giorni.")
    elif args.comando == "riepilogo":
        from .welcome import load_profile

        print(agenda.briefing(load_profile().get("name", ""), recent_files()))
    elif args.comando == "esporta":
        Path(args.file or "agenda.ics").write_text(agenda.export_ics())
        print(f"Agenda esportata in {args.file or 'agenda.ics'}")
    elif args.comando == "importa":
        if not args.file:
            print("Indica il file .ics da importare.")
            return 1
        print(f"Importati {agenda.import_ics(Path(args.file).read_text(errors='replace'))} eventi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
