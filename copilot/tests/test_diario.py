import sqlite3
from datetime import date, datetime, timedelta

from aios_copilot import diario
from aios_copilot.tools.memoria import MemoryRouter, make_tools

OGGI = date(2026, 10, 4)  # domenica


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, datetime.min.time()) + timedelta(hours=hh, minutes=mm)


def test_parse_day():
    assert diario.parse_day("ieri", OGGI) == date(2026, 10, 3)
    assert diario.parse_day("5 giorni fa", OGGI) == date(2026, 9, 29)
    assert diario.parse_day("5 gg fa", OGGI) == date(2026, 9, 29)
    assert diario.parse_day("tre giorni fa", OGGI) == date(2026, 10, 1)
    assert diario.parse_day("l'altro ieri", OGGI) == date(2026, 10, 2)
    assert diario.parse_day("lunedì", OGGI) == date(2026, 9, 28)
    assert diario.parse_day("domenica scorsa", OGGI) == date(2026, 9, 27)
    assert diario.parse_day("1/10", OGGI) == date(2026, 10, 1)
    assert diario.parse_day("boh", OGGI) is None


def test_record_and_summary(tmp_path):
    ieri = OGGI - timedelta(days=1)
    diario.record_exchange("ricordami il dentista ghp_" + "a" * 36, "Fatto", at(ieri, 9))
    diario.record("finestra", at(ieri, 10), app="org.libreoffice.LibreOffice", titolo="Preventivo cucina.odt")
    diario.record("finestra", at(ieri, 18), app="org.mozilla.firefox", titolo="Bici da corsa — Mozilla Firefox")
    diario.record_file("/home/u/Documenti/contratto.pdf", at(ieri, 11))
    text = diario.day_summary(ieri, OGGI, home=tmp_path)
    assert "Ecco ieri" in text and "Libreoffice" in text and "contratto.pdf" in text and "Preventivo cucina" in text
    assert "ghp_" not in (diario.page(ieri)).read_text()  # il segreto incollato non finisce nel diario
    assert (diario.page(ieri).stat().st_mode & 0o777) == 0o600


def test_disabled_records_nothing():
    diario.set_enabled(False)
    assert not diario.record("chat", at(OGGI, 9), domanda="x", risposta="y")
    assert diario.events(OGGI) == []


def test_where_left_off_and_search(tmp_path):
    ieri = OGGI - timedelta(days=1)
    diario.record("finestra", at(ieri, 22, 15), app="org.gnome.TextEditor", titolo="tesi capitolo 3")
    diario.record_exchange("riassumi il capitolo 3", "Ecco il riassunto…", at(ieri, 22, 20))
    text = diario.where_left_off(at(OGGI, 9), home=tmp_path)
    assert "ieri" in text and "tesi capitolo 3" in text and "riassumi il capitolo 3" in text
    found = diario.search("capitolo", today=OGGI, home=tmp_path)
    assert "tesi capitolo 3" in found


def _firefox(home, visits):
    prof = home / ".mozilla/firefox/abc.default"
    prof.mkdir(parents=True)
    con = sqlite3.connect(prof / "places.sqlite")
    con.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    con.execute("CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, place_id INTEGER, visit_date INTEGER)")
    for i, (when, title, url) in enumerate(visits, 1):
        con.execute("INSERT INTO moz_places VALUES (?, ?, ?)", (i, url, title))
        con.execute("INSERT INTO moz_historyvisits VALUES (?, ?, ?)", (i, i, int(when.timestamp() * 1e6)))
    con.commit()
    con.close()


def test_site_seen_days_ago(tmp_path):
    cinque = OGGI - timedelta(days=5)
    _firefox(tmp_path, [(at(cinque, 21), "Le migliori bici gravel 2026", "https://www.bikeitalia.it/gravel"),
                        (at(cinque, 21, 5), "Meteo Milano", "https://meteo.it/milano"),
                        (at(OGGI - timedelta(days=20), 9), "Bici vecchia", "https://old.example/bici")])
    found = diario.search("bici", day=diario.parse_day("5 giorni fa", OGGI), today=OGGI, home=tmp_path)
    assert "bikeitalia.it/gravel" in found and "old.example" not in found
    assert "bikeitalia.it" in diario.day_summary(cinque, OGGI, home=tmp_path)


def test_router():
    r = MemoryRouter()
    assert r.match("dove mi son fermato ieri?").tool == "recall_day"
    assert r.match("dove mi ero fermato?").tool == "where_left_off"
    i = r.match("mi ricordi su cosa ho lavorato ieri?")
    assert i.tool == "recall_day" and i.args == {"giorno": "ieri"}
    i = r.match("qual era quel sito sulle bici che ho visto 5 gg fa?")
    assert i.tool == "search_memory" and i.args == {"testo": "bici", "giorno": "5 gg fa"}
    assert r.match("riprendiamo la conversazione di ieri").args == {"giorno": "ieri"}
    assert r.match("spegni il diario").args == {"attivo": "no"}
    assert r.match("che ore sono") is None


def test_tools(tmp_path):
    tools = {t.name: t for t in make_tools()}
    assert "Non ho niente" in tools["recall_day"].func(giorno="ieri")
    assert "spento" in tools["diary_switch"].func(attivo="no")
    assert not diario.enabled()
    assert tools["forget_diary"].requires_confirmation
