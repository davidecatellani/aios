"""I documenti personali usati per rispondere: trovare il file giusto, leggerne la parte utile.

- «fammi vedere la bolletta di luglio della luce»: ricerca nell'indice personale, poi
  i risultati si riordinano capendo mesi («luglio», 07/2026), anni e argomento; un file
  di un altro mese scende in fondo.
- «cosa devo mangiare oggi?»: dal PDF della dieta si prende la parte del giorno (e del
  pasto) giusta, senza bisogno del modello AI.
- «fammi la lista della spesa»: gli alimenti della dieta raccolti, contati e divisi per
  reparto.

Tutto locale: i documenti non lasciano il dispositivo (dal telefono arriva solo il file
richiesto, sulla connessione cifrata con il PC).
"""

from __future__ import annotations

import contextvars
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

MONTHS = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
          "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12}
MONTH_ABBR = {m[:3]: n for m, n in MONTHS.items()}
WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
WEEKDAY_RE = re.compile(r"(?im)^[^\w\n]{0,6}(luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|venerd[iì]|sabato|domenica)\b")
MEALS = {"colazione": "colazione", "spuntino": "spuntino", "merenda": "spuntino", "pranzo": "pranzo", "cena": "cena"}
MEAL_RE = re.compile(r"(?i)\b(colazione|spuntino|merenda|pranzo|cena)\b")

# Chi consegna il documento trovato: sul PC lo apre; dal telefono diventa un pulsante «Apri».
deliver_to: contextvars.ContextVar[Callable[[Path], str] | None] = contextvars.ContextVar("deliver_to", default=None)


def hints(query: str, today: date) -> tuple[set[int], set[int]]:
    """Mesi e anni citati («luglio», «il mese scorso», «2025»)."""
    low = query.lower()
    months = {n for m, n in MONTHS.items() if re.search(rf"\b{m}\b", low)}
    if "mese scorso" in low:
        months.add((today.month - 2) % 12 + 1)
    if "questo mese" in low:
        months.add(today.month)
    years = {int(y) for y in re.findall(r"\b(20\d{2})\b", low)}
    return months, years


def month_mentions(text: str) -> set[int]:
    low = text.lower()
    found = {n for m, n in MONTHS.items() if re.search(rf"\b{m}\b", low)}
    found |= {n for m, n in MONTH_ABBR.items() if re.search(rf"(?:^|[\s_.-]){m}(?:$|[\s_.-])", low)}
    for d, m in re.findall(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](?:20)?\d{2}\b", low):
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            found.add(int(m))
    for y, m in re.findall(r"\b(20\d{2})[-_.](\d{2})\b", low):
        if 1 <= int(m) <= 12:
            found.add(int(m))
    return found


def find_documents(index: Any, query: str, today: date | None = None, limit: int = 5) -> list[tuple[Path, float]]:
    today = today or date.today()
    months, years = hints(query, today)
    results = index.search(query, limit=25)
    scored = []
    for rank, r in enumerate(results):
        path = Path(str(r["path"]))
        name = path.stem.lower().replace("_", " ").replace("-", " ")
        text = (index.read(path, 20000) or "")[:20000]
        score = 3.0 / (rank + 3)
        in_name, in_text = month_mentions(name), month_mentions(text[:3000])
        if months:
            if months & in_name:
                score += 2.0
            elif months & in_text:
                score += 1.0
            elif in_name - months:
                score -= 1.5  # il nome dice un altro mese: quasi certamente non è lui
        if years:
            score += 0.8 if any(str(y) in name for y in years) else 0.4 if any(str(y) in text[:3000] for y in years) else -0.3
        words = [w for w in re.findall(r"\w{4,}", query.lower()) if w not in MONTHS]
        score += 0.3 * sum(w[:-1] in name for w in words)
        if path.suffix.lower() == ".pdf":
            score += 0.2
        scored.append((path, score))
    return sorted(scored, key=lambda x: -x[1])[:limit]


# --- dieta e altri programmi a giorni ------------------------------------------------------------


def _norm_day(word: str) -> str:
    word = word.lower()
    return word[:-1] + "ì" if word.endswith("i") else word  # «lunedi» scritto senza accento


def section_for_day(text: str, day: date, meal: str = "") -> str | None:
    """La parte del documento per quel giorno della settimana (e quel pasto, se chiesto)."""
    heads = [(m.start(), _norm_day(m.group(1))) for m in WEEKDAY_RE.finditer(text)]
    wanted = WEEKDAYS[day.weekday()]
    section = None
    for i, (pos, name) in enumerate(heads):
        if name == wanted:
            end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
            section = text[pos:end].strip()
            break
    if section is None:
        return None
    if meal:
        lines, keep, out = section.splitlines(), False, []
        for line in lines[1:]:
            m = MEAL_RE.search(line)
            if m:
                keep = MEALS[m.group(1).lower()] == MEALS.get(meal, meal)
            if keep:
                out.append(line)
        if out:
            return "\n".join(out).strip()
    return section


def parse_when(text: str, now: datetime) -> tuple[date, str]:
    low = text.lower()
    day = now.date() + timedelta(days=1) if "domani" in low else now.date()
    for i, name in enumerate(WEEKDAYS):
        if name in low or name.replace("ì", "i") in low:
            day = now.date() + timedelta(days=(i - now.weekday()) % 7)
    meal = next((MEALS[w] for w in MEALS if w in low), "")
    if not meal and "stasera" in low:
        meal = "cena"
    return day, meal


QUANTITY = re.compile(r"^\s*(?:\d+[\d.,/]*\s*(?:g|gr|grammi|kg|ml|l|cl|cucchia\w*|fett\w*|vasett\w*|bicchier\w*|"
                      r"porzion\w*|pz|pezz\w*|tazz\w*|manciat\w*|scatolett\w*)?\.?\s*(?:di\s+|d')?|un[oa]?\s+|mezz[oa]\s+)",
                      re.I)
CATEGORIES = {
    "🥬 Frutta e verdura": ["mela", "mele", "pera", "banana", "arancia", "kiwi", "frutti", "frutta", "insalata", "pomodor",
                           "zucchin", "carot", "spinaci", "broccol", "verdur", "melanzan", "peperon", "finocch", "cipoll",
                           "lattuga", "rucola", "limone", "fragol", "uva", "patat", "cavol", "funghi", "legumi", "ceci",
                           "lenticchie", "fagioli", "piselli", "avocado", "frutti di bosco"],
    "🍞 Pane, pasta e cereali": ["pane", "pasta", "riso", "farro", "orzo", "avena", "fiocchi", "cereali", "fette biscottate",
                                "gallette", "cracker", "quinoa", "cous", "biscott", "muesli", "integrale"],
    "🥩 Carne e pesce": ["pollo", "tacchino", "manzo", "vitello", "maiale", "prosciutto", "bresaola", "salmone", "tonno",
                        "merluzzo", "pesce", "orata", "branzino", "gamber", "carne", "uova", "uovo"],
    "🧀 Latte e latticini": ["latte", "yogurt", "formaggio", "ricotta", "mozzarella", "parmigiano", "grana", "fiocchi di latte",
                            "kefir", "skyr"],
    "🥜 Altro": [],
}


def shopping_items(text: str) -> Counter:
    """Gli alimenti citati nelle parti dei pasti, senza quantità; contati (quante volte servono)."""
    items: Counter = Counter()
    for line in text.splitlines():
        line = MEAL_RE.sub("", WEEKDAY_RE.sub("", line)).strip(" :-•*\t")
        line = re.sub(r"\([^)]*\)", "", line)
        if not line or len(line) > 200 or re.match(r"(?i)^(dieta|piano|settimana|note|nota|dott|kcal|totale)", line):
            continue
        for part in re.split(r"[,;+]|\s+e\s+|\s+con\s+", line):
            part = re.sub(r"(?i)\bfette\s+biscottate\b", "zz_biscottate", part)  # è un alimento, non una quantità
            item = QUANTITY.sub("", part.strip()).strip(" .:-").lower().replace("zz_biscottate", "fette biscottate")
            item = re.sub(r"^(?:di|del|della|dei|delle)\s+", "", item)
            if 2 < len(item) <= 40 and not re.search(r"\d", item):
                items[item] += 1
    return items


def categorize(items: Counter) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {k: [] for k in CATEGORIES}
    for item, count in sorted(items.items()):
        cat = next((c for c, words in CATEGORIES.items() if any(w in item for w in words)), "🥜 Altro")
        groups[cat].append(f"{item} (×{count})" if count > 1 else item)
    return {k: v for k, v in groups.items() if v}


def shopping_text(groups: dict[str, list[str]]) -> str:
    return "\n".join(f"{cat}\n" + "\n".join(f"  ☐ {i}" for i in items) for cat, items in groups.items())
