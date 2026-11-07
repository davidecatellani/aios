"""I livelli della posta, come in un datalake (bronze → silver → gold), dentro il database della posta (SQLite).

- **bronze**: le mail come arrivano (tabella `messages`, store.py);
- **silver**: per ogni mail i fatti (tabella `mail_fatti`): tipo (da fare, informazione, pubblicità), chi, cosa,
  entro quando. Le mail di massa e quelle automatiche le decide il codice dalla categoria (classify.py), senza
  modello; le altre le legge il modello una alla volta, **in sottofondo**, cedendo il passo all'utente; la data la
  mette in ordine il codice (tabelle.parse_date);
- **gold**: le risposte pronte, fatte dal codice dal silver: le cose da fare in ordine di scadenza.

Così il lavoro lento del modello si fa quando l'utente non aspetta: alla domanda «cosa mi chiedono nelle mail?»
Nova risponde col gold in un attimo, invece di leggere ogni volta migliaia di parole di posta (su un PC modesto,
decine di secondi). docs/VELOCITA.md.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

from ..privacy import redact_secrets
from ..tabelle import MONTHS, parse_date
from .store import COLUMNS, Mail, MailStore, _row

VERSION = 1  # cambia quando cambia il modo di leggere le mail: le mail si rileggono
DAYS = 60  # si leggono le mail degli ultimi due mesi
SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_fatti (id INTEGER PRIMARY KEY, tipo TEXT, chi TEXT, cosa TEXT, entro TEXT,
                                       versione INTEGER, quando REAL);
CREATE INDEX IF NOT EXISTS mail_fatti_entro ON mail_fatti(tipo, entro);
"""
TYPES = ("da fare", "informazione", "pubblicità")
BY_CATEGORY = {"newsletter": "pubblicità", "notifiche": "informazione", "ricevute": "informazione"}  # senza modello
PROMPT = """Leggi una mail ricevuta dall'utente e rispondi solo con un JSON:
{"tipo": "da fare" se una persona chiede all'utente di fare qualcosa, "informazione" se avvisa o racconta, "pubblicità" se è un'offerta o una newsletter,
 "cosa": se è da fare, cosa deve fare l'utente in poche parole (per esempio «mandare il report trimestrale»), altrimenti "",
 "entro": la scadenza come è scritta nella mail (per esempio «12 ottobre»), oppure "" se non c'è}"""


def ensure(store: MailStore) -> None:
    store.db.executescript(SCHEMA)


def pending(store: MailStore, now: datetime | None = None, limit: int = 50) -> list[Mail]:
    """Le mail ricevute negli ultimi DAYS giorni non ancora lette per il silver (le più recenti prima)."""
    ensure(store)
    since = ((now or datetime.now()) - timedelta(days=DAYS)).isoformat()
    cols = ", ".join(f"m.{c.strip()}" for c in COLUMNS.split(","))
    rows = store.db.execute(
        f"SELECT {cols} FROM messages m LEFT JOIN mail_fatti f ON f.id = m.id "
        "WHERE (f.id IS NULL OR f.versione < ?) AND m.date >= ? AND m.folder NOT LIKE '%Sent%' AND m.folder NOT LIKE '%Inviat%' "
        "ORDER BY m.date DESC LIMIT ?", (VERSION, since, limit)).fetchall()
    return [_row(r) for r in rows]


def due_date(text: str, received: datetime) -> str:
    """«12 ottobre» → «2026-10-12» (l'anno, se manca, è quello della mail o il successivo se la data è già passata)."""
    found = parse_date(text or "")
    if not found:
        return ""
    year, month, day = found
    try:
        when = date(year or received.year, month, day)
        if not year and when < received.date() - timedelta(days=31):
            when = date(received.year + 1, month, day)
    except ValueError:
        return ""
    return when.isoformat()


def read_one(mail: Mail, model: Any) -> dict[str, str]:
    """I fatti di una mail. Le mail di massa e automatiche li ha già decisi il codice; le altre le legge il modello."""
    who = mail.sender_name or mail.sender
    if mail.category in BY_CATEGORY:
        return {"tipo": BY_CATEGORY[mail.category], "chi": who, "cosa": "", "entro": ""}
    text = (f"Da: {who} <{mail.sender}>\nData: {mail.date:%d/%m/%Y}\nOggetto: {mail.subject}\n\n"
            + redact_secrets(re.sub(r"\n{3,}", "\n\n", mail.body or mail.snippet)[:3000]))
    reply = model.chat([{"role": "system", "content": PROMPT}, {"role": "user", "content": text}], [])
    from ..bollette import loose_json

    data = loose_json(reply.get("content") or "")
    kind = str(data.get("tipo") or "").strip().lower()
    kind = next((t for t in TYPES if t in kind or kind.startswith(t[:6])), "informazione")
    what = str(data.get("cosa") or "").strip()
    due = due_date(str(data.get("entro") or ""), mail.date)
    if kind == "da fare" and not what:
        what = mail.subject
    return {"tipo": kind, "chi": who, "cosa": what if kind == "da fare" else "", "entro": due if kind == "da fare" else ""}


def save(store: MailStore, mail_id: int, facts: dict[str, str], clock: Callable[[], float] = time.time) -> None:
    ensure(store)
    store.db.execute("INSERT OR REPLACE INTO mail_fatti VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (mail_id, facts["tipo"], facts["chi"], facts["cosa"], facts["entro"], VERSION, clock()))


def update(store: MailStore, model: Any, wait: Callable[[], Any] = lambda: True, limit: int = 20,
           now: datetime | None = None) -> int:
    """Il silver per le mail nuove, al massimo `limit` per volta; prima di ogni mail cede il passo all'utente
    (`wait`). Se il modello non risponde ci si ferma e si riprova al giro dopo. → quante mail lette."""
    done = 0
    for mail in pending(store, now, limit):
        if mail.category not in BY_CATEGORY:
            wait()
        try:
            facts = read_one(mail, model)
        except Exception:  # modello spento o occupato: le mail restano da leggere
            break
        save(store, mail.id, facts)
        done += 1
    return done


def todo(store: MailStore, now: datetime | None = None, days: int | None = None) -> list[dict[str, Any]]:
    """Gold: le cose da fare dalla posta, non ancora scadute, in ordine di scadenza (quelle senza data in fondo)."""
    ensure(store)
    today = (now or datetime.now()).date()
    sql = ("SELECT f.id, f.chi, f.cosa, f.entro, m.subject, m.date FROM mail_fatti f JOIN messages m ON m.id = f.id "
           "WHERE f.tipo = 'da fare' AND (f.entro = '' OR f.entro >= ?)")
    args: list[Any] = [today.isoformat()]
    if days is not None:
        sql += " AND f.entro != '' AND f.entro <= ?"
        args.append((today + timedelta(days=days)).isoformat())
    sql += " ORDER BY f.entro = '', f.entro, m.date"
    return [{"id": r[0], "chi": r[1], "cosa": r[2], "entro": r[3], "oggetto": r[4], "ricevuta": r[5]}
            for r in store.db.execute(sql, args)]


def say_date(iso: str, today: date) -> str:
    d = date.fromisoformat(iso)
    if d == today:
        return "oggi"
    if d == today + timedelta(days=1):
        return "domani"
    return f"il {d.day} {MONTHS[d.month - 1]}" + (f" {d.year}" if d.year != today.year else "")


def todo_text(store: MailStore, now: datetime | None = None, days: int | None = None) -> str:
    """Il gold come lo dice Nova: già pronto, nessun modello."""
    now = now or datetime.now()
    items = todo(store, now, days)
    left = len(pending(store, now, limit=500))
    unread = f"\n(Ci sono ancora {left} mail che non ho letto: le leggo appena il computer è libero.)" if left else ""
    if not items:
        return ("Nella posta non trovo cose da fare" + (f" nei prossimi {days} giorni" if days else "") + "." + unread)
    lines = [f"Le cose da fare dalla posta{f' nei prossimi {days} giorni' if days else ''}:"]
    for n, it in enumerate(items, 1):
        when = f", entro {say_date(it['entro'], now.date())}" if it["entro"] else ""
        lines.append(f"{n}. {it['chi']}: {it['cosa']}{when} [{it['id']}]")
    return "\n".join(lines) + unread
