"""Date e orari in linguaggio naturale (italiano e inglese), interamente in locale.

    parse_when("ricordami di chiamare la mamma domani alle 18", now)
    → When(at=<domani 18:00>, all_day=False, repeat="", rest="chiamare la mamma")

Regole pratiche:
- solo l'ora ("alle 18"): oggi se è ancora futura, altrimenti domani;
- solo il giorno ("giovedì"): evento di tutta la giornata;
- "alle 3" senza "di mattina": le 15 (nessuno fissa appuntamenti alle 3 di notte);
- il giorno della settimana indica sempre il prossimo (oggi è giovedì → "giovedì" è tra 7 giorni,
  a meno che si dica "oggi").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

NUMBERS = {
    "un": 1, "uno": 1, "una": 1, "primo": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6,
    "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "undici": 11, "dodici": 12, "tredici": 13,
    "quattordici": 14, "quindici": 15, "sedici": 16, "diciassette": 17, "diciotto": 18,
    "diciannove": 19, "venti": 20, "ventuno": 21, "ventidue": 22, "ventitré": 23, "ventitre": 23,
    "trenta": 30, "quaranta": 40, "cinquanta": 50,
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
}
NUM = r"(?:\d{1,2}|" + "|".join(sorted(NUMBERS, key=len, reverse=True)) + r")"

MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
    "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6, "lug": 7, "ago": 8, "set": 9,
    "ott": 10, "nov": 11, "dic": 12,
}
MONTH = r"(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")"

WEEKDAYS = {
    "lunedì": 0, "lunedi": 0, "martedì": 1, "martedi": 1, "mercoledì": 2, "mercoledi": 2,
    "giovedì": 3, "giovedi": 3, "venerdì": 4, "venerdi": 4, "sabato": 5, "domenica": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}
WEEKDAY = r"(?:" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")"

# Parti del giorno: ora predefinita se non è detta, e se l'ora detta è del pomeriggio/sera.
PARTS = {
    "mattina": (9, False), "mattino": (9, False), "morning": (9, False),
    "pomeriggio": (15, True), "afternoon": (15, True),
    "sera": (20, True), "evening": (20, True), "notte": (23, True), "night": (23, True),
}
PART = r"(?:di |del |della |in |nel |in the )?(?P<part>mattina|mattino|pomeriggio|sera|notte|morning|afternoon|evening|night)"

UNITS = {"min": 1, "minut": 1, "or": 60, "hour": 60, "giorn": 1440, "day": 1440, "settiman": 10080, "week": 10080}

RE_RELATIVE = re.compile(
    r"\b(?:tra|fra|entro|in)\s+(?:(?P<half>mezz'?\s?ora|mezz’ora|half an hour)|(?P<quarter>un quarto d'?ora)"
    rf"|(?P<n>{NUM})\s*(?P<unit>minut[oi]|min|or[ae]|giorn[oi]|settiman[ae]|minutes?|hours?|days?|weeks?))\b"
)
RE_DAYWORD = re.compile(
    r"\b(?P<w>oggi|stamattina|stamani|stasera|stanotte|domattina|domani|dopodomani|today|tonight|tomorrow)"
    rf"(?:\s+{PART})?\b"
)
RE_WEEKDAY = re.compile(rf"\b(?:(?P<next>next)\s+)?(?P<wd>{WEEKDAY})(?:\s+(?:prossimo|prossima))?(?:\s+{PART})?\b")
RE_DATE = re.compile(
    rf"\b(?:il\s+|on\s+(?:the\s+)?)?(?P<d>\d{{1,2}}|primo)(?:°|º)?\s*(?:(?:di\s+)?(?P<mon>{MONTH})\b(?:\s+(?P<y1>\d{{4}}))?"
    r"|/(?P<m>\d{1,2})(?:/(?P<y>\d{2,4}))?)"
)
RE_DAY_OF_MONTH = re.compile(r"\b(?:il|on the)\s+(?P<d>\d{1,2}|primo)\b(?!\s*(?:/|:|\.\d|minut|or[ae]|giorn))")
RE_TIME = re.compile(
    rf"\b(?:alle|all'|alla|ore|verso le|per le|at|by)\s*(?P<h>{NUM}|mezzogiorno|mezzanotte|noon|midnight)"
    r"(?:(?:[:.](?P<m>\d{2}))|\s+e\s+(?P<mw>mezza|mezzo|un quarto|tre quarti|\d{1,2}))?"
    rf"(?:\s*(?P<ampm>am|pm)\b)?(?:\s+{PART})?"
)
RE_BARE_TIME = re.compile(r"\b(?P<h>\d{1,2})[:.](?P<m>\d{2})\b")
RE_NOON = re.compile(r"\b(?:a\s+)?(?P<h>mezzogiorno|mezzanotte)\b")
RE_REPEAT = re.compile(
    rf"\b(?:ogni|tutti i|tutte le|every)\s+(?P<what>giorno|giorni|mattina|mattine|sera|sere|day|morning|evening"
    rf"|settimana|week|mese|month|anno|year|(?P<wd>{WEEKDAY}))\b"
)
RE_PART_ALONE = re.compile(rf"\b{PART}\b")


@dataclass
class When:
    at: datetime | None
    all_day: bool = False
    repeat: str = ""  # "", "daily", "weekly", "monthly", "yearly"
    rest: str = ""


def _num(token: str) -> int:
    return int(token) if token.isdigit() else NUMBERS[token]


def _next_weekday(today: date, weekday: int) -> date:
    days = (weekday - today.weekday()) % 7
    return today + timedelta(days=days or 7)


def parse_iso(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def parse_when(text: str, now: datetime | None = None) -> When:
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    low = text.lower().replace("’", "'")
    spans: list[tuple[int, int]] = []

    def take(m: re.Match[str] | None) -> re.Match[str] | None:
        if m:
            spans.append(m.span())
        return m

    repeat = ""
    day: date | None = None
    hour_min: tuple[int, int] | None = None
    part_hint: str | None = None

    # Ricorrenze: "ogni giorno", "tutti i lunedì"
    m = take(RE_REPEAT.search(low))
    if m:
        what = m.group("what")
        if m.group("wd"):
            repeat, day = "weekly", _next_weekday(now.date(), WEEKDAYS[m.group("wd")])
        elif what.startswith(("giorn", "day", "mattin", "morning", "ser", "evening")):
            repeat = "daily"
            if what.startswith(("mattin", "morning")):
                part_hint = "mattina"
            elif what.startswith(("ser", "evening")):
                part_hint = "sera"
        elif what.startswith(("settiman", "week")):
            repeat = "weekly"
        elif what.startswith(("mes", "month")):
            repeat = "monthly"
        else:
            repeat = "yearly"

    # Tempo relativo: "tra 20 minuti", "fra due ore"
    m = take(RE_RELATIVE.search(low))
    if m:
        if m.group("half"):
            minutes = 30
        elif m.group("quarter"):
            minutes = 15
        else:
            unit = next(v for k, v in UNITS.items() if m.group("unit").startswith(k))
            minutes = _num(m.group("n")) * unit
        return When(now + timedelta(minutes=minutes), False, repeat, _rest(text, spans))

    # Giorno
    m = take(RE_DAYWORD.search(low))
    if m:
        w = m.group("w")
        offset = {"domani": 1, "domattina": 1, "tomorrow": 1, "dopodomani": 2}.get(w, 0)
        day = now.date() + timedelta(days=offset)
        part_hint = m.group("part") or {"stamattina": "mattina", "stamani": "mattina", "domattina": "mattina",
                                        "stasera": "sera", "tonight": "sera", "stanotte": "notte"}.get(w, part_hint)
    if day is None:
        m = take(RE_WEEKDAY.search(low))
        if m:
            day = _next_weekday(now.date(), WEEKDAYS[m.group("wd")])
            part_hint = m.group("part") or part_hint
    if day is None:
        m = take(RE_DATE.search(low))
        if m:
            d = 1 if m.group("d") == "primo" else int(m.group("d"))
            month = MONTHS[m.group("mon")] if m.group("mon") else int(m.group("m"))
            year_txt = m.group("y1") or m.group("y")
            year = (int(year_txt) + (2000 if len(year_txt) == 2 else 0)) if year_txt else now.year
            try:
                day = date(year, month, d)
                if not year_txt and day < now.date():
                    day = date(year + 1, month, d)
            except ValueError:
                day = None
    if day is None:
        m = take(RE_DAY_OF_MONTH.search(low))
        if m:
            d = 1 if m.group("d") == "primo" else int(m.group("d"))
            month, year = now.month, now.year
            for _ in range(13):  # "il 31" in un mese di 30 giorni: il prossimo mese che ce l'ha
                try:
                    candidate = date(year, month, d)
                    if candidate >= now.date():
                        day = candidate
                        break
                except ValueError:
                    pass
                month, year = (1, year + 1) if month == 12 else (month + 1, year)

    # Ora
    m = take(RE_TIME.search(low)) or take(RE_NOON.search(low)) or take(RE_BARE_TIME.search(low))
    if m:
        h_txt = m.group("h")
        if h_txt in ("mezzogiorno", "noon"):
            hour, minute = 12, 0
        elif h_txt in ("mezzanotte", "midnight"):
            hour, minute = 0, 0
        else:
            hour = _num(h_txt)
            groups = m.groupdict()
            minute = int(groups["m"]) if groups.get("m") else 0
            mw = groups.get("mw")
            if mw:
                minute = {"mezza": 30, "mezzo": 30, "un quarto": 15, "tre quarti": 45}.get(mw) or int(mw)
            part = groups.get("part") or part_hint
            ampm = groups.get("ampm")
            if ampm == "pm" and hour < 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            elif not ampm and hour < 12:
                if part and PARTS[part][1]:
                    hour += 12
                elif not part and 1 <= hour <= 7 and not groups.get("m"):
                    hour += 12  # "alle 3" = le 15
        if 0 <= hour < 24 and 0 <= minute < 60:
            hour_min = (hour, minute)
    elif part_hint is None:
        m = take(RE_PART_ALONE.search(low))
        if m and day is not None:
            part_hint = m.group("part")

    if hour_min is None and part_hint and (day is not None or repeat):
        hour_min = (PARTS[part_hint][0], 0)

    rest = _rest(text, spans)
    if day is None and hour_min is None:
        if repeat in ("weekly", "monthly", "yearly"):
            return When(datetime.combine(now.date() + timedelta(days=1), time(9)), True, repeat, rest)
        return When(None, False, repeat, rest)
    if day is None:
        at = datetime.combine(now.date(), time(*hour_min))
        if at <= now:
            at += timedelta(days=1)
        return When(at, False, repeat, rest)
    if hour_min is None:
        return When(datetime.combine(day, time(9)), True, repeat, rest)
    return When(datetime.combine(day, time(*hour_min)), False, repeat, rest)


_TRIGGERS = re.compile(
    r"^(?:ehi\s+|per favore\s+|puoi\s+)?(?:ricordami|ricorda(?:mi)?|promemoria|remind me|reminder|non farmi dimenticare|"
    r"segna(?:mi)?|aggiungi|metti|fissa|crea|inserisci|add|schedule)\b\s*(?:in agenda\s+|nel calendario\s+)?"
    r"(?:(?:di|che|to|un|una|un'|l'|il|lo|la|that)\s+)*",
    re.I,
)
_EDGE_WORDS = re.compile(
    r"^(?:(?:di|che|a|per|il|lo|la|le|l'|e|ed|to|on|at|the|alle|ore|del|della|dal|in|ogni|tutti|tutte)\s+)+"
    r"|(?:\s+(?:di|che|a|per|il|lo|la|le|e|ed|to|on|at|the|alle|ore|del|della|dal|in|ogni|tutti|tutte|i|entro|by))+$",
    re.I,
)


def _rest(text: str, spans: list[tuple[int, int]]) -> str:
    """Il testo senza le espressioni di tempo e le parole di comando: il titolo del promemoria."""
    chars = list(text)
    for start, end in spans:
        for i in range(start, end):
            chars[i] = " "
    rest = re.sub(r"\s+", " ", "".join(chars)).strip(" ,.;:!?")
    for _ in range(3):
        rest = _TRIGGERS.sub("", rest).strip(" ,.;:!?")
        rest = _EDGE_WORDS.sub("", rest).strip(" ,.;:!?")
    return re.sub(r"\s+", " ", rest).strip()


def describe(at: datetime | None, all_day: bool = False, now: datetime | None = None, lang: str = "it") -> str:
    """"oggi alle 18:00", "domani", "giovedì 12 novembre alle 10:30"."""
    if at is None:
        return "senza data"
    now = now or datetime.now()
    days = (at.date() - now.date()).days
    names = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
    months = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
              "settembre", "ottobre", "novembre", "dicembre"]
    if days == 0:
        d = "oggi"
    elif days == 1:
        d = "domani"
    elif days == 2:
        d = "dopodomani"
    elif 0 < days < 7:
        d = names[at.weekday()]
    else:
        d = f"{names[at.weekday()]} {at.day} {months[at.month - 1]}" + (f" {at.year}" if at.year != now.year else "")
    return d if all_day else f"{d} alle {at:%H:%M}"
