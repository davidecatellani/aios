"""Documenti e bollette a livelli, come in un datalake (bronze → silver → gold), in SQLite. docs/VELOCITA.md.

- **bronze**: i documenti dell'utente e il loro testo, nell'indice dei file (fileindex.py);
- **silver**: per ogni documento i fatti (tabella `documenti_fatti`): tipo (bolletta, fattura, avviso di pagamento,
  multa, ricevuta, altro), servizio (luce, gas, acqua…), emittente, numero, data, scadenza, totale, addebito
  automatico. Che un documento *non* sia da pagare lo decide il codice (nessuna parola da bolletta o nessun
  importo); gli altri li legge il modello, uno alla volta, quando il PC è libero (learning.Scheduler) e
  interrompendosi se l'utente parla con Nova (precedenza.BackgroundModel). I numeri li controlla il codice:
  l'importo e la scadenza devono esserci davvero nel documento, altrimenti si cercano accanto a «totale da pagare»
  e «scadenza»; l'addebito automatico lo riconosce il codice («domiciliazione», «addebito diretto», SDD);
- **gold**: le risposte pronte, fatte dal codice: le bollette da pagare in ordine di scadenza, quanto si è speso
  per un servizio in un anno. Nova le dice subito, senza leggere i documenti mentre l'utente aspetta.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .fileindex import data_dir
from .privacy import private_dir
from .tabelle import MONTHS, fmt, numbers_in, parse_number

VERSION = 2  # 2: JSON del modello letto con tolleranza, tipo e fornitore anche dal testo
DOC_EXT = (".pdf", ".txt", ".md", ".docx", ".odt", ".html", ".htm")
TYPES = ("bolletta", "fattura", "avviso di pagamento", "multa", "ricevuta", "altro")
PAYABLE = ("bolletta", "fattura", "avviso di pagamento", "multa")
SERVICES = ("luce", "gas", "acqua", "telefono", "internet", "rifiuti", "assicurazione", "tasse", "altro")
SERVICE_WORDS = {"energia elettrica": "luce", "elettricità": "luce", "corrente": "luce", "luce": "luce", "gas": "gas",
                 "acqua": "acqua", "idrico": "acqua", "telefon": "telefono", "cellulare": "telefono", "mobile": "telefono",
                 "internet": "internet", "fibra": "internet", "adsl": "internet", "rifiuti": "rifiuti", "tari": "rifiuti",
                 "assicura": "assicurazione", "polizza": "assicurazione", "rc auto": "assicurazione", "bollo": "tasse",
                 "imu": "tasse", "f24": "tasse", "tasse": "tasse"}
CANDIDATE = re.compile(r"\b(bollett\w*|fattur\w*|avviso di pagamento|da pagare|importo|pagopa|f24|multa|verbale|premio|"
                       r"rata|tari|canone|scadenza)\b", re.I)
MONEY = re.compile(r"€|\beur(?:o)?\b", re.I)
DIRECT_DEBIT = re.compile(r"domiciliazion\w*|addebito\s+(?:diretto|automatico|su\s+conto|in\s+conto|sepa)|\bsdd\b|\brid\b|"
                          r"sar[àa]\s+addebitat\w*", re.I)
TOTAL_NEAR = re.compile(r"(?:totale\s+(?:da\s+pagare|bolletta|fattura|documento|dovuto)|importo\s+(?:da\s+pagare|totale|dovuto)|"
                        r"rata\s+\d+\s+di\s+\d+|da\s+pagare)[^\d\n]{0,40}?(\d[\d.,]*\d)", re.I)
KIND_WORDS = (("avviso di pagamento", r"avviso\s+di\s+pagamento"), ("multa", r"\bmulta\b|verbale\s+di\s+(?:accertamento|contestazione)"),
              ("fattura", r"\bfattura\b"), ("bolletta", r"\bbolletta\b"), ("ricevuta", r"\bricevuta\b"))
DUE_NEAR = re.compile(r"(?:scadenz\w*|scade|entro\s+il|da\s+pagare\s+entro|pagare\s+entro)[^\d\n]{0,30}?"
                      r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}\s+(?:" + "|".join(MONTHS) + r")(?:\s+\d{4})?)", re.I)
DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b|\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b|"
                     r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?", re.I)
PROMPT = """Leggi il documento e rispondi solo con un JSON con queste chiavi:
{"tipo": "bolletta", "fattura", "avviso di pagamento", "multa", "ricevuta" oppure "altro",
 "servizio": "luce", "gas", "acqua", "telefono", "internet", "rifiuti", "assicurazione", "tasse" oppure "altro",
 "emittente": chi lo manda (per esempio «Enel Energia»),
 "numero": il numero del documento o della fattura, "data": la data del documento,
 "scadenza": entro quando va pagato, "totale": l'importo totale da pagare (solo il numero)}
Le date come sono scritte nel documento; se una cosa non c'è, "". Non inventare."""


def db_path() -> Path:
    return private_dir(data_dir()) / "documenti.db"


def dates_in(text: str) -> list[date]:
    """Tutte le date scritte in un testo (con l'anno)."""
    out = []
    for m in DATE_RE.finditer(text):
        try:
            if m.group(1):
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif m.group(4):
                y = int(m.group(6))
                d = date(y + 2000 if y < 100 else y, int(m.group(5)), int(m.group(4)))
            else:
                if not m.group(9):
                    continue
                d = date(int(m.group(9)), MONTHS.index(m.group(8).lower()) + 1, int(m.group(7)))
        except ValueError:
            continue
        out.append(d)
    return out


def to_date(text: str, known: list[date], ref: date | None = None) -> date | None:
    """Una data scritta dal modello, tenuta solo se c'è davvero nel documento (`known`)."""
    found = dates_in(text or "")
    if not found and text:
        m = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})\b|\b(\d{1,2})\s+(" + "|".join(MONTHS) + ")", text, re.I)
        if m:  # senza anno: quello della data del documento che ha giorno e mese uguali
            day, month = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(3)), MONTHS.index(m.group(4).lower()) + 1)
            found = [d for d in known if (d.day, d.month) == (day, month)]
    for d in found:
        if d in known:
            return d
    return None


def loose_json(reply: str) -> dict[str, Any]:
    """Il JSON del modello, anche quando non è valido («"totale": 77,07», virgole in fondo): prima com'è, poi chiave
    per chiave."""
    m = re.search(r"\{.*\}", reply or "", re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except ValueError:
        pass
    out: dict[str, Any] = {}
    for k, quoted, bare in re.findall(r'"(\w+)"\s*:\s*(?:"((?:[^"\\]|\\.)*)"|([^,"}\n]+(?:,\d{1,2}\b)?))', m.group(0)):
        out[k] = quoted if quoted or not bare else bare.strip()
    return out


def code_only(text: str) -> bool:
    """Il codice decide da solo che non è un documento da pagare (niente parole da bolletta o niente importi)."""
    return not CANDIDATE.search(text) or not MONEY.search(text)


def read_document(text: str, model: Any) -> dict[str, Any]:
    """I fatti di un documento: il modello legge, il codice controlla."""
    empty = {"tipo": "altro", "servizio": "", "emittente": "", "numero": "", "data": "", "scadenza": "", "totale": None,
             "addebito": 0, "controllo": ""}
    if code_only(text):
        return empty
    reply = model.chat([{"role": "system", "content": PROMPT}, {"role": "user", "content": text[:6000]}], [])
    data = loose_json(reply.get("content") or "")
    kind = str(data.get("tipo") or "").strip().lower()
    kind = next((t for t in TYPES if t in kind), "")
    if not kind or (kind == "altro" and TOTAL_NEAR.search(text)):  # il modello non si capisce: il tipo dalle parole
        kind = next((k for k, rx in KIND_WORDS if re.search(rx, text, re.I)), "altro")
    low = text.lower()
    service = str(data.get("servizio") or "").strip().lower()
    if service not in SERVICES:
        service = next((s for w, s in SERVICE_WORDS.items() if w in service), "altro")
    if re.search(r"\btari\b|rifiuti", low):
        service = "rifiuti"  # la TARI è una tassa, ma chi chiede «quanto ho speso di rifiuti» la vuole qui
    if service == "altro":  # il modello non l'ha detto: si guarda il documento
        service = next((s for w, s in SERVICE_WORDS.items() if re.search(rf"\b{w}", low)), "altro")
    issuer = str(data.get("emittente") or "").strip()
    if not issuer:  # di solito il nome del fornitore è la prima riga
        issuer = next((l.strip() for l in text.splitlines() if l.strip()), "")[:60]
    known = dates_in(text)
    problems = []
    total = parse_number(str(data.get("totale") or ""))
    amounts = numbers_in(text)
    if total is not None and not any(abs(total - a) < 0.005 for a in amounts):
        problems.append(f"il totale {fmt(total)} non è nel documento")
        total = None
    if total is None:
        near = TOTAL_NEAR.search(text)
        total = parse_number(near.group(1)) if near else None
    doc_date = to_date(str(data.get("data") or ""), known)
    due = to_date(str(data.get("scadenza") or ""), known)
    if due is None:
        near = DUE_NEAR.search(text)
        due = to_date(near.group(1), known) if near else None
        if data.get("scadenza") and due is None:
            problems.append("la scadenza non è nel documento")
    return {"tipo": kind, "servizio": service if kind != "altro" else "", "emittente": issuer[:80] if kind != "altro" else "",
            "numero": str(data.get("numero") or "").strip()[:40], "data": doc_date.isoformat() if doc_date else "",
            "scadenza": due.isoformat() if due else "", "totale": total, "addebito": int(bool(DIRECT_DEBIT.search(text))),
            "controllo": "; ".join(problems)}


class Facts:
    """Il silver e il gold dei documenti."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS documenti_fatti (
        path TEXT PRIMARY KEY, mtime REAL, tipo TEXT, servizio TEXT, emittente TEXT, numero TEXT, data TEXT,
        scadenza TEXT, totale REAL, addebito INTEGER, controllo TEXT, versione INTEGER, quando REAL);
    CREATE INDEX IF NOT EXISTS documenti_scadenza ON documenti_fatti(tipo, scadenza);
    """

    def __init__(self, path: Path | None = None):
        self.db = sqlite3.connect(path or db_path(), check_same_thread=False, isolation_level=None)
        self.db.executescript(self.SCHEMA)

    # --- silver ---------------------------------------------------------------------------------------------
    def pending(self, index: Any, limit: int = 50) -> list[tuple[str, float]]:
        """I documenti dell'indice nuovi o cambiati dall'ultima lettura (i più recenti prima)."""
        files = index.db.execute("SELECT path, mtime FROM files WHERE status = 'ok' ORDER BY mtime DESC").fetchall()
        known = {p: (m, v) for p, m, v in self.db.execute("SELECT path, mtime, versione FROM documenti_fatti")}
        out = []
        for path, mtime in files:
            if not path.lower().endswith(DOC_EXT):
                continue
            seen = known.get(path)
            if seen is None or seen[0] != mtime or seen[1] < VERSION:
                out.append((path, mtime))
                if len(out) >= limit:
                    break
        return out

    def save(self, path: str, mtime: float, facts: dict[str, Any], clock: Callable[[], float] = time.time) -> None:
        self.db.execute("INSERT OR REPLACE INTO documenti_fatti VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (path, mtime, facts["tipo"], facts["servizio"], facts["emittente"], facts["numero"], facts["data"],
                         facts["scadenza"], facts["totale"], facts["addebito"], facts["controllo"], VERSION, clock()))

    def update(self, index: Any, model: Any, limit: int = 20, deadline: float | None = None) -> int:
        """Legge i documenti nuovi: quelli che il codice scarta subito, tutti; col modello, al massimo `limit`. Se il
        modello non risponde ci si ferma (si riprova al giro dopo). → quanti documenti letti."""
        done = used = 0
        for path, mtime in self.pending(index, limit=500):
            if deadline is not None and time.monotonic() > deadline and done:
                break
            text = index.read(Path(path), max_chars=8000) or ""
            if not code_only(text):
                if used >= limit:
                    continue
                used += 1
            try:
                facts = read_document(text, model)
            except Exception:  # modello spento o occupato: il documento resta da leggere
                break
            self.save(path, mtime, facts)
            done += 1
        return done

    # --- gold -----------------------------------------------------------------------------------------------
    def to_pay(self, now: datetime | None = None, days: int | None = 30) -> list[dict[str, Any]]:
        """Da pagare: bollette, fatture, avvisi e multe con scadenza da oggi a `days` giorni, senza addebito automatico."""
        today = (now or datetime.now()).date()
        sql = (f"SELECT path, servizio, emittente, scadenza, totale FROM documenti_fatti WHERE tipo IN ({','.join('?' * len(PAYABLE))}) "
               "AND addebito = 0 AND scadenza >= ?")
        args: list[Any] = [*PAYABLE, today.isoformat()]
        if days is not None:
            sql += " AND scadenza <= ?"
            args.append((today + timedelta(days=days)).isoformat())
        return [{"path": r[0], "servizio": r[1], "emittente": r[2], "scadenza": r[3], "totale": r[4]}
                for r in self.db.execute(sql + " ORDER BY scadenza", args)]

    def automatic(self, now: datetime | None = None, days: int | None = 30) -> list[dict[str, Any]]:
        today = (now or datetime.now()).date()
        end = (today + timedelta(days=days or 3650)).isoformat()
        return [{"emittente": r[0], "scadenza": r[1], "totale": r[2]} for r in self.db.execute(
            f"SELECT emittente, scadenza, totale FROM documenti_fatti WHERE addebito = 1 AND tipo IN ({','.join('?' * len(PAYABLE))}) "
            "AND scadenza >= ? AND scadenza <= ? ORDER BY scadenza", (*PAYABLE, today.isoformat(), end))]

    def spent(self, service: str = "", year: int | None = None, issuer: str = "") -> list[dict[str, Any]]:
        """Le bollette e fatture di un servizio (o di un emittente) in un anno: per data del documento, o di scadenza."""
        sql = ("SELECT path, emittente, COALESCE(NULLIF(data, ''), scadenza) AS giorno, totale FROM documenti_fatti "
               f"WHERE tipo IN ({','.join('?' * len(PAYABLE))}) AND totale IS NOT NULL")
        args: list[Any] = [*PAYABLE]
        if service:
            sql += " AND servizio = ?"
            args.append(service)
        if issuer:
            sql += " AND lower(emittente) LIKE ?"
            args.append(f"%{issuer.lower()}%")
        if year:
            sql += " AND substr(giorno, 1, 4) = ?"
            args.append(str(year))
        return [{"path": r[0], "emittente": r[1], "quando": r[2], "totale": r[3]} for r in self.db.execute(sql + " ORDER BY giorno", args)]

    def unread(self, index: Any) -> int:
        return len(self.pending(index, limit=1000))


def say_date(iso: str, today: date) -> str:
    d = date.fromisoformat(iso)
    if d == today:
        return "oggi"
    if d == today + timedelta(days=1):
        return "domani"
    return f"il {d.day} {MONTHS[d.month - 1]}" + (f" {d.year}" if d.year != today.year else "")


def to_pay_text(facts: Facts, now: datetime | None = None, days: int | None = 30, unread: int = 0) -> str:
    now = now or datetime.now()
    items = facts.to_pay(now, days)
    span = f" nei prossimi {days} giorni" if days else ""
    note = f"\n(Ci sono ancora {unread} documenti che non ho letto: li leggo quando il computer è libero.)" if unread else ""
    auto = facts.automatic(now, days)
    auto_line = ("\nCon addebito automatico (non devi fare niente): " + "; ".join(
        f"{a['emittente'] or 'senza nome'} {fmt(a['totale']) + ' €' if a['totale'] is not None else ''} {say_date(a['scadenza'], now.date())}".replace("  ", " ")
        for a in auto) + ".") if auto else ""
    if not items:
        return f"Non trovo bollette da pagare{span}." + auto_line + note
    lines = [f"Da pagare{span}:"]
    total = 0.0
    for n, it in enumerate(items, 1):
        what = it["emittente"] or Path(it["path"]).name
        service = f" ({it['servizio']})" if it["servizio"] and it["servizio"] != "altro" else ""
        amount = f"{fmt(it['totale'])} €" if it["totale"] is not None else "importo da controllare"
        total += it["totale"] or 0
        lines.append(f"{n}. {what}{service}: {amount}, entro {say_date(it['scadenza'], now.date())} — {Path(it['path']).name}")
    if len(items) > 1:
        lines.append(f"In tutto {fmt(total)} €.")
    return "\n".join(lines) + auto_line + note


def spent_text(facts: Facts, service: str = "", year: int | None = None, now: datetime | None = None, unread: int = 0) -> str:
    now = now or datetime.now()
    year = year or now.year
    service = service.strip().lower()
    if re.match(r"(?:le\s+)?bollett", service):  # «di bollette»: tutte
        service = ""
    known = SERVICE_WORDS.get(service) or next((s for w, s in SERVICE_WORDS.items() if w in service), "")
    rows = facts.spent(known, year) if known or not service else facts.spent("", year, issuer=service)
    label = known or service or "bollette e fatture"
    note = f"\n(Ci sono ancora {unread} documenti che non ho letto.)" if unread else ""
    if not rows:
        return f"Non trovo bollette di {label} nel {year}." + note
    total = sum(r["totale"] for r in rows)
    months = "; ".join(f"{MONTHS[int(r['quando'][5:7]) - 1]} {fmt(r['totale'])}" for r in rows if r["quando"])
    return (f"{label.capitalize()} nel {year}: {fmt(total)} € in {len(rows)} "
            f"{'documento' if len(rows) == 1 else 'documenti'}" + (f" ({months})" if months else "") + "." + note)


class BillsTask:
    """Il silver dei documenti come lavoro di sottofondo (learning.Scheduler: PC libero, in carica, Nova non in uso)."""

    name = "bollette e scadenze nei documenti"

    def __init__(self, index: Any, facts: Facts | None = None, model: Callable[[], Any] | None = None):
        self.index, self._facts, self._model = index, facts, model
        self._checked, self._has = 0.0, False
        self._retry_at = 0.0

    @property
    def facts(self) -> Facts:
        if self._facts is None:
            self._facts = Facts()
        return self._facts

    def available(self) -> bool:
        return time.monotonic() >= self._retry_at

    def has_work(self) -> bool:
        if time.monotonic() - self._checked > 60:  # contare i documenti nuovi costa: al massimo una volta al minuto
            self._has, self._checked = bool(self.facts.pending(self.index, limit=1)), time.monotonic()
        return self._has

    def step(self, seconds: float) -> None:
        if self._model is None:
            from .precedenza import BackgroundModel

            self._model = BackgroundModel
        before = len(self.facts.pending(self.index, limit=1000))
        self.facts.update(self.index, self._model(), limit=1, deadline=time.monotonic() + seconds)
        self._checked = 0.0
        if len(self.facts.pending(self.index, limit=1000)) == before:  # niente di fatto (modello spento): si riprova tra 10 minuti
            self._retry_at = time.monotonic() + 600
