"""Il diario di SoIA: Nova ricorda cosa hai fatto, per rispondere a «dove mi ero fermato?»,
«su cosa ho lavorato ieri?», «che sito era quello sulle bici che ho visto 5 giorni fa?».

Cosa si annota, un file per giorno in ~/.local/share/aios/diario (leggibile solo dall'utente):
- le conversazioni con Nova (domanda e risposta; i segreti incollati sono già tolti);
- i programmi aperti e i titoli delle loro finestre (la shell guarda ogni mezzo minuto);
- i file e i documenti aperti dalle app di SoIA.
I siti visitati non si copiano: si leggono, quando servono, dalla cronologia di Firefox (che non
contiene la navigazione anonima). Niente esce dal computer. Il diario si spegne o si cancella da
Impostazioni › Privacy o chiedendolo a Nova; le pagine più vecchie di KEEP_DAYS si buttano da sole.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

KEEP_DAYS = 90
_lock = threading.Lock()

STOPWORDS = set("""il lo la i gli le un uno una di del dello della dei degli delle a al allo alla ai agli alle da dal
dallo dalla dai dagli dalle in nel nello nella nei negli nelle su sul sullo sulla sui sugli sulle con per tra fra
che chi cosa quel quello quella quelli quelle questo questa e ed o ma se mi ti ci si ho hai ha era ero sono sito
siti pagina pagine visto vista vista guardato letto aperto quale qual giorni giorno fa ieri oggi l un' dove""".split())


# --- impostazioni ------------------------------------------------------------------------------------------
def settings_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "diario.json"


def enabled() -> bool:
    try:
        return bool(json.loads(settings_path().read_text()).get("attivo", True))
    except (OSError, ValueError, AttributeError):
        return True


def set_enabled(on: bool) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"attivo": bool(on)}))


def diary_dir() -> Path:
    from .agenda import data_dir
    from .privacy import private_dir

    return private_dir(data_dir() / "diario")


def page(day: date) -> Path:
    return diary_dir() / f"{day.isoformat()}.jsonl"


# --- scrittura ---------------------------------------------------------------------------------------------
def record(kind: str, now: datetime | None = None, **data: Any) -> bool:
    if not enabled():
        return False
    now = now or datetime.now()
    line = json.dumps({"t": now.isoformat(timespec="seconds"), "tipo": kind, **data}, ensure_ascii=False)
    with _lock:
        path = page(now.date())
        fresh = not path.exists()
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if fresh:
            os.chmod(path, 0o600)
            purge(now.date())
    return True


def record_exchange(question: str, answer: str, now: datetime | None = None) -> bool:
    from .agent import redact_secrets

    return record("chat", now, domanda=redact_secrets(question)[:500], risposta=redact_secrets(answer)[:800])


def record_file(path: Path | str, now: datetime | None = None) -> bool:
    return record("file", now, percorso=str(path))


def purge(today: date | None = None) -> None:
    limit = (today or date.today()) - timedelta(days=KEEP_DAYS)
    for p in diary_dir().glob("*.jsonl"):
        try:
            if date.fromisoformat(p.stem) < limit:
                p.unlink()
        except ValueError:
            continue


def forget_all() -> int:
    n = 0
    with _lock:
        for p in diary_dir().glob("*.jsonl"):
            p.unlink()
            n += 1
    return n


class WindowWatcher:
    """Annota i programmi aperti e i titoli nuovi delle finestre (es. la pagina che Firefox mostra)."""

    def __init__(self, windows: Callable[[], list[dict[str, str]]], clock: Callable[[], datetime] = datetime.now):
        self.windows, self.clock = windows, clock
        self.seen: OrderedDict[tuple[str, str], None] = OrderedDict()

    def tick(self) -> int:
        new = 0
        for w in self.windows():
            key = (w.get("app_id", ""), w.get("title", ""))
            if key in self.seen:
                self.seen.move_to_end(key)
                continue
            self.seen[key] = None
            if len(self.seen) > 200:
                self.seen.popitem(last=False)
            if record("finestra", self.clock(), app=key[0], titolo=key[1][:200]):
                new += 1
        return new

    def run(self, every: float = 30.0) -> None:
        while True:
            try:
                self.tick()
            except Exception:  # il diario non deve mai fermare la shell
                pass
            time.sleep(every)


# --- lettura -----------------------------------------------------------------------------------------------
def events(day: date) -> list[dict[str, Any]]:
    try:
        lines = page(day).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def days_with_events() -> list[date]:
    days = []
    for p in diary_dir().glob("*.jsonl"):
        try:
            days.append(date.fromisoformat(p.stem))
        except ValueError:
            continue
    return sorted(days)


WEEKDAYS = ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"]
NUMBERS = {"un": 1, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6, "sette": 7,
           "otto": 8, "nove": 9, "dieci": 10, "quindici": 15, "venti": 20, "trenta": 30}


def _plain(text: str) -> str:
    return (text.lower().replace("à", "a").replace("è", "e").replace("é", "e").replace("ì", "i")
            .replace("ò", "o").replace("ù", "u").strip())


def parse_day(text: str, today: date | None = None) -> date | None:
    """«oggi», «ieri», «l'altro ieri», «5 giorni fa», «lunedì», «3/10», «2026-10-01»."""
    today = today or date.today()
    t = _plain(text)
    if not t or t in ("oggi", "stamattina", "stasera", "stamani"):
        return today
    if t in ("ieri", "ieri sera", "ieri mattina"):
        return today - timedelta(days=1)
    if t in ("l'altro ieri", "altro ieri", "altroieri", "l'altroieri"):
        return today - timedelta(days=2)
    m = re.fullmatch(r"(\d+|\w+)\s*(?:gg|giorni|giorno)\s+fa", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else NUMBERS.get(m.group(1))
        return today - timedelta(days=n) if n is not None else None
    m = re.fullmatch(r"(?:una|la)\s+settimana\s+fa", t)
    if m:
        return today - timedelta(days=7)
    m = re.fullmatch(r"(?:(?:lo|la)\s+)?(?:scors[oa]\s+)?(\w+)(?:\s+scors[oa])?", t)
    if m and m.group(1) in WEEKDAYS:
        back = (today.weekday() - WEEKDAYS.index(m.group(1))) % 7 or 7
        return today - timedelta(days=back)
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", t)
    if m:
        year = int(m.group(3)) if m.group(3) else today.year
        year += 2000 if year < 100 else 0
        try:
            return date(year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    try:
        return date.fromisoformat(t)
    except ValueError:
        return None


def day_name(day: date, today: date | None = None) -> str:
    today = today or date.today()
    diff = (today - day).days
    if diff == 0:
        return "oggi"
    if diff == 1:
        return "ieri"
    names = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
    return f"{names[day.weekday()]} {day.day}/{day.month}"


def app_name(app_id: str) -> str:
    last = app_id.rsplit(".", 1)[-1] if "." in app_id else app_id
    return last.replace("-", " ").replace("_", " ").strip().capitalize() or app_id


# --- cronologia di Firefox ---------------------------------------------------------------------------------
def firefox_profiles(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    found = []
    for base in (home / ".mozilla/firefox", home / ".var/app/org.mozilla.firefox/.mozilla/firefox"):
        found += [p for p in base.glob("*/places.sqlite")]
    return found


def browser_history(since: datetime, until: datetime, home: Path | None = None,
                    limit: int = 400) -> list[dict[str, Any]]:
    """Le pagine visitate tra due momenti (la più recente prima). Si legge una copia del database:
    quello vero è bloccato finché Firefox è aperto."""
    out: list[dict[str, Any]] = []
    for db in firefox_profiles(home):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                copy = Path(tmp) / "places.sqlite"
                shutil.copy2(db, copy)
                wal = db.with_name("places.sqlite-wal")
                if wal.exists():
                    shutil.copy2(wal, Path(tmp) / "places.sqlite-wal")
                con = sqlite3.connect(copy)
                rows = con.execute(
                    "SELECT v.visit_date, p.title, p.url FROM moz_historyvisits v JOIN moz_places p ON p.id = v.place_id "
                    "WHERE v.visit_date BETWEEN ? AND ? ORDER BY v.visit_date DESC LIMIT ?",
                    (int(since.timestamp() * 1e6), int(until.timestamp() * 1e6), limit)).fetchall()
                con.close()
            except (OSError, sqlite3.Error):
                continue
        for when, title, url in rows:
            if url and url.startswith(("http://", "https://")):
                out.append({"t": datetime.fromtimestamp(when / 1e6), "titolo": title or "", "url": url})
    out.sort(key=lambda v: v["t"], reverse=True)
    return out


def site(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url


# --- risposte ----------------------------------------------------------------------------------------------
def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " e " + items[-1]


def day_summary(day: date, today: date | None = None, home: Path | None = None) -> str:
    evs = events(day)
    start = datetime.combine(day, datetime.min.time())
    visits = browser_history(start, start + timedelta(days=1), home)
    when = day_name(day, today)
    if not evs and not visits:
        return f"Non ho niente nel diario per {when}." + ("" if enabled() else " Il diario è spento.")
    parts = [f"Ecco {when}:" if when in ("oggi", "ieri") else f"Ecco cosa hai fatto {when}:"]
    apps = Counter(app_name(e["app"]) for e in evs if e["tipo"] == "finestra" and e.get("app"))
    if apps:
        times = [e["t"][11:16] for e in evs if e["tipo"] == "finestra"]
        parts.append(f"• Programmi: {_join([a for a, _ in apps.most_common(6)])} (dalle {times[0]} alle {times[-1]}).")
    files = list(OrderedDict.fromkeys(Path(e["percorso"]).name for e in evs if e["tipo"] == "file"))
    if files:
        parts.append(f"• File aperti: {_join(files[-6:])}.")
    titles = list(OrderedDict.fromkeys(e["titolo"] for e in evs if e["tipo"] == "finestra" and e.get("titolo")
                                       and "firefox" not in e.get("app", "").lower()))
    if titles:
        parts.append(f"• Documenti e finestre: {_join(titles[-5:])}.")
    if visits:
        sites = Counter(site(v["url"]) for v in visits)
        parts.append(f"• Siti più visitati: {_join([s for s, _ in sites.most_common(5)])}.")
    chats = [e["domanda"] for e in evs if e["tipo"] == "chat"]
    if chats:
        parts.append(f"• Mi hai chiesto: {_join(['«' + c[:70] + '»' for c in chats[-4:]])}.")
    return "\n".join(parts)


def where_left_off(now: datetime | None = None, home: Path | None = None) -> str:
    """L'ultima cosa fatta prima di adesso: l'ultima giornata con qualcosa nel diario (anche oggi)."""
    now = now or datetime.now()
    days = [d for d in days_with_events() if d <= now.date()]
    if not days:
        return "Il diario è ancora vuoto: da adesso ricordo io cosa fai." if enabled() else "Il diario è spento."
    day = days[-1]
    evs = events(day)
    when = day_name(day, now.date())
    lines = [f"L'ultima volta ({when}, verso le {evs[-1]['t'][11:16]}):"]
    last_windows = list(OrderedDict.fromkeys(
        f"{app_name(e['app'])} – {e['titolo']}" if e.get("titolo") else app_name(e["app"])
        for e in reversed(evs) if e["tipo"] == "finestra"))[:3]
    if last_windows:
        lines.append(f"• avevi aperto {_join(last_windows)}")
    last_files = list(OrderedDict.fromkeys(Path(e["percorso"]).name for e in reversed(evs) if e["tipo"] == "file"))[:3]
    if last_files:
        lines.append(f"• stavi guardando {_join(last_files)}")
    chats = [e for e in evs if e["tipo"] == "chat"]
    if chats:
        lines.append(f"• l'ultima cosa che mi hai chiesto: «{chats[-1]['domanda'][:120]}»")
    start = datetime.combine(day, datetime.min.time())
    visits = browser_history(start, start + timedelta(days=1), home, limit=3)
    if visits:
        lines.append(f"• l'ultima pagina: {visits[0]['titolo'] or site(visits[0]['url'])} ({visits[0]['url']})")
    return "\n".join(lines)


def keywords(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", _plain(text)) if len(w) > 2 and w not in STOPWORDS]


def _score(words: list[str], *fields: str) -> int:
    hay = _plain(" ".join(f for f in fields if f))
    return sum(1 for w in words if w in hay)


def search(text: str, day: date | None = None, days: int = 30, today: date | None = None,
           home: Path | None = None) -> str:
    """Cerca nel diario e nella cronologia: in un giorno preciso (con un giorno di margine) o negli ultimi giorni."""
    today = today or date.today()
    words = keywords(text)
    if day is not None:
        span = [day - timedelta(days=1), day, min(day + timedelta(days=1), today)]
    else:
        span = [today - timedelta(days=i) for i in range(days)]
    span = sorted(set(span))
    start = datetime.combine(span[0], datetime.min.time())
    end = datetime.combine(span[-1], datetime.min.time()) + timedelta(days=1)
    hits: list[tuple[int, datetime, str]] = []
    seen: set[str] = set()
    for v in browser_history(start, end, home, limit=3000):
        s = _score(words, v["titolo"], v["url"]) if words else 1
        if s and v["url"] not in seen:
            seen.add(v["url"])
            hits.append((s, v["t"], f"{v['titolo'] or site(v['url'])} — {v['url']}"))
    for d in span:
        for e in events(d):
            t = datetime.fromisoformat(e["t"])
            if e["tipo"] == "chat":
                s, label = _score(words, e["domanda"], e["risposta"]), f"mi hai chiesto «{e['domanda'][:80]}»"
            elif e["tipo"] == "file":
                s, label = _score(words, e["percorso"]), f"il file {e['percorso']}"
            else:
                s, label = _score(words, e.get("titolo", ""), e.get("app", "")), \
                    f"{app_name(e.get('app', ''))}: {e.get('titolo', '')}"
            if (s or not words) and label not in seen:
                seen.add(label)
                hits.append((s, t, label))
    if not hits:
        where = day_name(day, today) if day else f"negli ultimi {days} giorni"
        what = f" su «{' '.join(words)}»" if words else ""
        return f"Non trovo niente{what} {where}."
    hits.sort(key=lambda h: (-h[0], -h[1].timestamp()))
    lines = ["Ho trovato:"] + [f"• {day_name(t.date(), today)} alle {t:%H:%M}: {label}" for _, t, label in hits[:5]]
    return "\n".join(lines)


def conversation(day: date, limit: int = 20) -> list[dict[str, str]]:
    """Le ultime domande e risposte di un giorno, per riprendere la conversazione."""
    return [{"domanda": e["domanda"], "risposta": e["risposta"], "ora": e["t"][11:16]}
            for e in events(day) if e["tipo"] == "chat"][-limit:]


def recent_context(now: datetime | None = None, limit: int = 6) -> list[dict[str, str]]:
    """Gli ultimi scambi con Nova (oggi, o l'ultimo giorno), da rimettere nella memoria dell'agente."""
    now = now or datetime.now()
    for day in reversed([d for d in days_with_events() if d <= now.date()][-3:]):
        chats = conversation(day, limit)
        if chats:
            return chats
    return []

