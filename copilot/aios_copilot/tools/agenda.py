"""Agenda e promemoria: strumenti per il copilota e riconoscimento istantaneo delle frasi."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Callable, Iterable

from ..agenda import REPEATS, Agenda, recent_files
from ..fastpath import Intent, normalize
from ..when import describe, parse_iso, parse_when
from .base import Tool, params

REPEAT_TEXT = {"daily": "ogni giorno", "weekly": "ogni settimana", "monthly": "ogni mese", "yearly": "ogni anno"}


def _when(value: str, now: datetime) -> tuple[datetime | None, bool, str]:
    """Data da ISO (dal modello) o da linguaggio naturale (dall'utente)."""
    if not value:
        return None, False, ""
    iso = parse_iso(value)
    if iso:
        return iso, len(value.strip()) == 10, ""
    w = parse_when(value, now)
    return w.at, w.all_day, w.repeat


def _period(period: str, now: datetime) -> tuple[datetime, datetime, str]:
    p = (period or "oggi").lower().strip()
    today = datetime.combine(now.date(), time())
    if p in ("oggi", "today", ""):
        return today, today + timedelta(days=1), "oggi"
    if p in ("domani", "tomorrow"):
        return today + timedelta(days=1), today + timedelta(days=2), "domani"
    if p in ("settimana", "questa settimana", "week", "prossimi giorni"):
        return today, today + timedelta(days=7), "nei prossimi 7 giorni"
    if p in ("weekend", "fine settimana", "questo weekend"):
        sat = today + timedelta(days=(5 - now.weekday()) % 7)
        return sat, sat + timedelta(days=2), "nel fine settimana"
    at = parse_iso(p) or parse_when(p, now).at
    if at:
        start = datetime.combine(at.date(), time())
        return start, start + timedelta(days=1), describe(start, True, now)
    return today, today + timedelta(days=1), "oggi"


def make_tools(get_agenda: Callable[[], Agenda], get_name: Callable[[], str] = lambda: "",
               recent: Callable[[], Iterable[str]] = recent_files,
               extras: Callable[[], Iterable[str]] = lambda: ()) -> list[Tool]:
    def add_reminder(what: str, when: str = "", repeat: str = "") -> str:
        agenda = get_agenda()
        now = agenda.now()
        at, _, parsed_repeat = _when(when, now)
        repeat = repeat if repeat in REPEATS and repeat else parsed_repeat
        if when and at is None:
            return f"Non ho capito quando: «{when}». Prova con «domani alle 18» o «tra 20 minuti»."
        if at is not None and at < now - timedelta(minutes=1) and not repeat:
            return f"{describe(at, False, now)} è già passato: dimmi un altro momento."
        agenda.add_reminder(what.strip(), at, repeat)
        if at is None:
            return f"Aggiunto alle cose da fare: «{what}»."
        again = f" ({REPEAT_TEXT[repeat]})" if repeat else ""
        return f"Fatto: ti ricorderò «{what}» {describe(at, False, now)}{again}."

    def add_event(title: str, when: str, location: str = "", repeat: str = "") -> str:
        agenda = get_agenda()
        now = agenda.now()
        at, all_day, parsed_repeat = _when(when, now)
        if at is None:
            return f"Non ho capito quando: «{when}»."
        repeat = repeat if repeat in REPEATS and repeat else parsed_repeat
        agenda.add_event(title.strip(), at, all_day=all_day, location=location, repeat=repeat)
        where = f" ({location})" if location else ""
        again = f", {REPEAT_TEXT[repeat]}" if repeat else ""
        tail = "Ti avviso il giorno prima e il giorno stesso." if all_day else "Ti avviso il giorno prima e un'ora prima."
        return f"In agenda: «{title}»{where} {describe(at, all_day, now)}{again}. {tail}"

    def list_agenda(period: str = "oggi") -> str:
        agenda = get_agenda()
        now = agenda.now()
        if period.lower().strip() in ("promemoria", "da fare", "todo", "reminders"):
            upcoming = [i for i in agenda.between(now, now + timedelta(days=30)) if i.kind == "reminder"]
            todos = agenda.todos()
            lines = [i.line(now) for i in upcoming] + [f"🔔 da fare: {i.title}" for i in todos]
            return "\n".join(lines) or "Non hai promemoria."
        start, end, label = _period(period, now)
        items = agenda.between(start, end)
        if not items:
            return f"Nessun impegno {label}."
        return f"Ecco cosa hai {label}:\n" + "\n".join(i.line(now) for i in items)

    def daily_briefing() -> str:
        text = get_agenda().briefing(get_name(), recent())
        more = [line for line in extras() if line]  # es. modelli AI migliori disponibili
        return "\n".join([text, *more])

    def _pick(query: str) -> tuple[list, str | None]:
        found = get_agenda().find(query)
        if not found:
            return found, f"Non trovo niente in agenda che corrisponda a «{query}»."
        if len(found) > 1:
            now = get_agenda().now()
            return found, "Ho trovato più elementi, quale intendi?\n" + "\n".join(i.line(now) for i in found[:6])
        return found, None

    def complete_reminder(query: str) -> str:
        found, problem = _pick(query)
        if problem:
            return problem
        item = found[0]
        if item.kind != "reminder":
            return f"«{item.title}» è un appuntamento, non un promemoria."
        get_agenda().complete(item.id)
        return f"Segnato come fatto: «{item.title}». 👍"

    def delete_agenda_item(query: str) -> str:
        found, problem = _pick(query)
        if problem:
            return problem
        item = found[0]
        get_agenda().delete(item.kind, item.id)
        return f"Eliminato: «{item.title}»."

    def resolve_suggestion(number: str, accept: str = "sì") -> str:
        if not str(number).isdigit():
            return "Indica il numero della scadenza proposta."
        yes = str(accept).lower() in ("sì", "si", "yes", "true", "1", "accetta")
        item = get_agenda().resolve_suggestion(int(number), yes)
        if item is None:
            return f"Non trovo la proposta numero {number}."
        if not yes:
            return f"Va bene, ignoro «{item.title}»."
        return f"Aggiunto: ti ricorderò «{item.title}» {describe(item.at, False, get_agenda().now())}."

    repeat_desc = ("Ripetizione: vuoto, daily, weekly, monthly o yearly", list(REPEATS))
    return [
        Tool("add_reminder", "Crea un promemoria. 'when' in formato ISO (2025-11-12T18:00) o vuoto per una cosa da fare senza data.",
             params(["what"], what="Cosa ricordare", when="Quando (ISO)", repeat=repeat_desc), add_reminder),
        Tool("add_event", "Aggiunge un appuntamento in agenda. 'when' in formato ISO; solo la data per tutto il giorno.",
             params(["title", "when"], title="Titolo", when="Inizio (ISO)", location="Luogo", repeat=repeat_desc), add_event),
        Tool("list_agenda", "Mostra gli impegni: 'oggi', 'domani', 'settimana', 'weekend', 'promemoria' o una data ISO.",
             params(period="Periodo"), list_agenda),
        Tool("daily_briefing", "Riepilogo della giornata: impegni, cose da fare, scadenze trovate nei documenti.",
             params(), daily_briefing),
        Tool("complete_reminder", "Segna come fatto un promemoria, cercandolo per titolo.",
             params(query="Parole del titolo"), complete_reminder),
        Tool("delete_agenda_item", "Elimina un appuntamento o un promemoria, cercandolo per titolo.",
             params(query="Parole del titolo"), delete_agenda_item, requires_confirmation=True),
        Tool("resolve_suggestion", "Accetta o ignora una scadenza proposta dal riepilogo (numero tra parentesi).",
             params(number="Numero della proposta", accept=("sì per aggiungerla, no per ignorarla", ["sì", "no"])),
             resolve_suggestion),
    ]


# --- Riconoscimento istantaneo (livello 0) ------------------------------------------------

EVENT_WORDS = (r"appuntamento|riunione|visita|incontro|evento|cena|pranzo|colloquio|esame|lezione|call|"
               r"meeting|appointment|dentista|medico|compleanno|partita|aperitivo|udienza")
DAYS = r"oggi|domani|dopodomani|settimana|weekend|fine settimana|lunedì|martedì|mercoledì|giovedì|venerdì|sabato|domenica|today|tomorrow|week"

RE_BRIEFING = re.compile(
    r"^(?:buongiorno|good morning|riepilogo(?: della giornata| di oggi)?|dammi il riepilogo|"
    r"com'è la (?:mia )?giornata|come (?:è|sarà) la (?:mia )?giornata|organizzami la giornata|"
    r"cosa mi aspetta oggi|organizza la mia giornata|briefing)$"
)
RE_LIST = re.compile(
    rf"^(?:(?:cosa|che cosa|che|quali|quanti)\s+(?:ho|c'è|devo fare|impegni ho|appuntamenti ho|impegni|appuntamenti)"
    rf"|(?:mostra(?:mi)?|fammi vedere|leggimi|apri)\s+(?:l'|la\s+)?(?:agenda|calendario|impegni|appuntamenti|promemoria)"
    rf"|(?:ho|ci sono)\s+(?:impegni|appuntamenti)|(?:i\s+)?miei\s+(?:impegni|appuntamenti|promemoria)"
    rf"|(?:agenda|impegni|programma)\s+(?:di|della|del|per)|what's on|what do i have).*$"
)
RE_PERIOD = re.compile(rf"\b(?P<p>{DAYS})\b")
RE_REMIND = re.compile(r"\b(?:ricordami|ricordarmi|remind me|non farmi dimenticare)\b|^promemoria\b\s*[:,]?\s*(?:di|per|che|:)")
RE_EVENT = re.compile(
    rf"^(?:(?:segna(?:mi)?|aggiungi|metti|fissa|crea|inserisci|prenota|schedule|add)\s+(?:in agenda\s+|nel calendario\s+)?"
    rf"(?:un\s+|una\s+|un'|l'|il\s+|la\s+)?|ho\s+(?:un\s+|una\s+|un'|l'|il\s+|la\s+)?)?(?:{EVENT_WORDS})\b"
)
RE_SUGGESTION = re.compile(r"^(?P<verb>aggiungi|accetta|metti in agenda|ignora|scarta|rifiuta)\s+(?:la\s+)?(?:scadenza|proposta)\s+(?P<n>\d+)$")
RE_DONE = re.compile(r"^(?:segna come fatto|ho fatto|fatto:?|completato:?|ho finito di)\s+(?P<x>.+)$")
RE_DELETE = re.compile(
    rf"^(?:cancella|elimina|togli|annulla|rimuovi)\s+(?:l'|il\s+|lo\s+|la\s+)?(?:(?:appuntamento|promemoria|evento|impegno)\s+(?:del(?:la|lo|l')?\s+|di\s+)?)"
    rf"(?P<x>.+)$"
)


class AgendaRouter:
    """Frasi d'agenda riconosciute all'istante; nei casi dubbi decide il livello successivo."""

    def __init__(self, clock: Callable[[], datetime] = datetime.now):
        self.clock = clock

    def match(self, text: str) -> Intent | None:
        low = normalize(text)
        if RE_BRIEFING.match(low):
            return Intent("daily_briefing", {})
        m = RE_SUGGESTION.match(low)
        if m:
            accept = "no" if m.group("verb") in ("ignora", "scarta", "rifiuta") else "sì"
            return Intent("resolve_suggestion", {"number": m.group("n"), "accept": accept})
        if RE_LIST.match(low) and not RE_REMIND.search(low):
            if re.search(r"\bpromemoria\b|\bda fare\b", low) and not RE_PERIOD.search(low):
                return Intent("list_agenda", {"period": "promemoria"})
            p = RE_PERIOD.search(low)
            period = p.group("p") if p else "oggi"
            return Intent("list_agenda", {"period": {"fine settimana": "weekend", "today": "oggi", "tomorrow": "domani",
                                                     "week": "settimana"}.get(period, period)})
        m = RE_DONE.match(low)
        if m:
            return Intent("complete_reminder", {"query": m.group("x")})
        m = RE_DELETE.match(low)
        if m:
            return Intent("delete_agenda_item", {"query": m.group("x")})
        now = self.clock()
        if RE_REMIND.search(low):
            w = parse_when(text, now)
            if not w.rest:
                return None
            return Intent("add_reminder", {"what": w.rest, "when": w.at.isoformat() if w.at else "", "repeat": w.repeat})
        if RE_EVENT.match(low):
            w = parse_when(text, now)
            if w.at is None or not w.rest:
                return None  # manca il quando: meglio chiedere (LLM)
            when = w.at.date().isoformat() if w.all_day else w.at.isoformat()
            title = re.sub(r"^(?:ho|abbiamo|i have)\s+(?:(?:un|una|il|la|lo|an?)\s+|un'|l')?", "", w.rest, flags=re.I)
            return Intent("add_event", {"title": title[0].upper() + title[1:], "when": when, "repeat": w.repeat})
        return None
