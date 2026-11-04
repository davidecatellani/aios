"""I numeri come si dicono: «84,20 €» → «ottantaquattro euro e venti», «alle 10:30» → «alle dieci e trenta»,
«il 12/10» → «il dodici ottobre», «22%» → «ventidue per cento».

Le voci che leggono come un modello linguistico (Sibilia-TTS) sulle cifre inciampano, e anche le altre dicono
«ottantaquattro virgola venti euro». Qui le cifre diventano parole prima di parlare. Codici, targhe, numeri di
telefono e numeri molto lunghi restano come sono (si leggono cifra per cifra, ed è giusto così).
"""

from __future__ import annotations

import re

UNITS = ["zero", "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove", "dieci", "undici",
         "dodici", "tredici", "quattordici", "quindici", "sedici", "diciassette", "diciotto", "diciannove"]
TENS = ["", "", "venti", "trenta", "quaranta", "cinquanta", "sessanta", "settanta", "ottanta", "novanta"]
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
          "novembre", "dicembre"]


def _below_100(n: int) -> str:
    if n < 20:
        return UNITS[n]
    tens, unit = divmod(n, 10)
    word = TENS[tens]
    if unit in (1, 8):
        word = word[:-1]  # ventuno, ventotto
    return word + ("" if unit == 0 else "tré" if unit == 3 else UNITS[unit])


def _below_1000(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    head = "" if hundreds == 0 else "cento" if hundreds == 1 else UNITS[hundreds] + "cento"
    if not rest:
        return head
    tail = _below_100(rest)
    if head and tail.startswith("o"):  # centotto, centottanta
        head = head[:-1]
    return head + tail


def number(n: int) -> str:
    """Un intero in parole (fino a 999 milioni): 1234 → «milleduecentotrentaquattro»."""
    words = _number(n)
    return words[:-3] + "tré" if n > 20 and words.endswith("tre") else words  # ventitré, centotré, milletré


def _number(n: int) -> str:
    if n < 0:
        return "meno " + number(-n)
    if n < 1000:
        return _below_1000(n) if n else "zero"
    if n < 1_000_000:
        thousands, rest = divmod(n, 1000)
        head = "mille" if thousands == 1 else re.sub(r"uno$", "un", _below_1000(thousands)) + "mila"  # ventunmila
        return head + (_below_1000(rest) if rest else "")
    millions, rest = divmod(n, 1_000_000)
    head = "un milione" if millions == 1 else _below_1000(millions) + " milioni"
    return head + (" " + number(rest) if rest else "")


def _int(text: str) -> int:
    return int(text.replace(".", ""))


INT = r"(?:\d{1,3}(?:\.\d{3})+|\d{1,7})"
MONEY_AFTER = re.compile(rf"(?<![\w.,/-])({INT})(?:,(\d{{1,2}}))?\s*(?:€|euro\b|EUR\b)", re.I)
MONEY_BEFORE = re.compile(rf"(?:€|EUR)\s*({INT})(?:,(\d{{1,2}}))?(?![\d,])", re.I)
TIME = re.compile(r"(?<![\w.,:/-])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
DATE = re.compile(r"(?<![\w./-])(0?[1-9]|[12]\d|3[01])/(0?[1-9]|1[0-2])(?:/(\d{4}|\d{2}))?(?![\w/])")
PERCENT = re.compile(rf"(?<![\w.,-])({INT})(?:,(\d{{1,2}}))?\s*%")
DECIMAL = re.compile(rf"(?<![\w.,/:-])({INT}),(\d{{1,3}})(?![\w,.]*\d)")
PHONE = re.compile(r"(?<![\w.,/-])(\+?\d[\d ]{6,}\d)(?![\w]|[.,]\d)")
INTEGER = re.compile(rf"(?<![\w.,/:+-])((?:[1-9]\d{{0,2}}(?:\.\d{{3}})+)|[1-9]\d{{0,6}}|0)(?![\w,/:-]|\.\d)")


def _money(units: str, cents: str | None) -> str:
    whole = _int(units)
    words = "un euro" if whole == 1 else f"{number(whole)} euro"
    if cents:
        c = int(cents.ljust(2, "0"))
        if c:
            return f"{words} e {number(c)}" if whole else f"{number(c)} centesimi"
    return words


def _decimal(units: str, decimals: str) -> str:
    return f"{number(_int(units))} virgola " + (number(int(decimals)) if not decimals.startswith("0") or len(decimals) == 1
                                                 else " ".join(UNITS[int(d)] for d in decimals))


def _date(day: str, month: str, year: str | None) -> str:
    words = ("primo" if int(day) == 1 else number(int(day))) + " " + MONTHS[int(month) - 1]
    if year:
        y = int(year) if len(year) == 4 else 2000 + int(year)
        words += " " + number(y)
    return words


def _phone(m: re.Match) -> str:
    raw = m.group(1)
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 8 or (" " not in raw.strip() and not raw.startswith(("0", "3", "+"))):
        return raw  # non sembra un numero di telefono
    plus = "più " if raw.startswith("+") else ""
    return plus + ", ".join(" ".join(UNITS[int(d)] for d in g) for g in raw.lstrip("+").split())


def in_words(text: str) -> str:
    """Il testo con prezzi, percentuali, ore, date e numeri scritti in parole; i telefoni cifra per cifra."""
    text = PHONE.sub(_phone, text)
    text = MONEY_AFTER.sub(lambda m: _money(m.group(1), m.group(2)), text)
    text = MONEY_BEFORE.sub(lambda m: _money(m.group(1), m.group(2)), text)
    text = PERCENT.sub(lambda m: (_decimal(m.group(1), m.group(2)) if m.group(2) else number(_int(m.group(1)))) + " per cento", text)
    text = TIME.sub(lambda m: number(int(m.group(1))) + ("" if m.group(2) == "00" else " e " + number(int(m.group(2)))), text)
    text = DATE.sub(lambda m: _date(m.group(1), m.group(2), m.group(3)), text)
    text = DECIMAL.sub(lambda m: _decimal(m.group(1), m.group(2)), text)
    return INTEGER.sub(lambda m: number(_int(m.group(1))), text)
