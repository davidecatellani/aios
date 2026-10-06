import json
import sqlite3
from datetime import datetime

import pytest

from aios_copilot import azioni
from aios_copilot.tools.azioni import ActionsRouter, make_tools
from aios_copilot.tools.base import Tool, params


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    return tmp_path


def test_files_part_undo_and_conflict(home):
    f = home / "conf" / "aios" / "widget.json"
    f.parent.mkdir(parents=True)
    f.write_text("[]")
    reg = {"widget": azioni.files("widget", lambda: [f])}
    azioni.TOUCHES["prova_widget"] = ["widget"]
    try:
        azioni.record("prova_widget", {"tipo": "meteo"}, lambda: f.write_text('[{"tipo": "meteo"}]') and "ok", reg)
        azioni.record("prova_widget", {"tipo": "nota"}, lambda: f.write_text('[{"tipo": "nota"}]') and "ok", reg)
        items = azioni.pending()
        assert [i["descrizione"] for i in items] == ["prova_widget: nota", "prova_widget: meteo"]
        # la più vecchia non si annulla: dopo è cambiata di nuovo
        ok, msg = azioni.undo(items[1]["id"], reg)
        assert not ok and "di nuovo" in msg and f.read_text() == '[{"tipo": "nota"}]'
        # dalla più recente si torna indietro passo passo, fino a com'era all'inizio
        assert azioni.undo(items[0]["id"], reg)[0] and f.read_text() == '[{"tipo": "meteo"}]'
        assert azioni.undo(items[1]["id"], reg)[0] and f.read_text() == "[]"
        assert azioni.pending() == [] and not azioni.undo(items[1]["id"], reg)[0]
    finally:
        del azioni.TOUCHES["prova_widget"]


def test_nothing_changed_nothing_recorded(home):
    f = home / "x.json"
    f.write_text("{}")
    reg = {"x": azioni.files("x", lambda: [f])}
    azioni.TOUCHES["prova_x"] = ["x"]
    try:
        assert azioni.record("prova_x", {}, lambda: "Non trovo quel widget.", reg) == "Non trovo quel widget."
        assert azioni.load() == []
    finally:
        del azioni.TOUCHES["prova_x"]


def test_sqlite_rows_add_delete_change(home):
    db = home / "agenda.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT, start TEXT, notes TEXT)")
        c.execute("INSERT INTO events VALUES (1, 'Dentista', '2026-10-07T17:30', 'portare lastra')")
        c.execute("INSERT INTO events VALUES (2, 'Calcetto', '2026-10-08T20:00', '')")
    part = azioni.rows("agenda", lambda: db, "events", ["title", "start"])
    reg = {"agenda": part}
    azioni.TOUCHES["prova_agenda"] = ["agenda"]

    def change():
        with sqlite3.connect(db) as c:
            c.execute("INSERT INTO events VALUES (3, 'Cena', '2026-10-07T21:00', '')")
            c.execute("DELETE FROM events WHERE id = 2")
            c.execute("UPDATE events SET start = '2026-10-09T17:30' WHERE id = 1")
        return "fatto"

    try:
        azioni.record("prova_agenda", {"title": "Cena"}, change, reg)
        assert azioni.undo_last(registry=reg) == "Annullato: «prova_agenda: Cena»."
        with sqlite3.connect(db) as c:
            got = c.execute("SELECT id, title, start, notes FROM events ORDER BY id").fetchall()
        # le colonne non seguite (le note) restano come sono
        assert got == [(1, "Dentista", "2026-10-07T17:30", "portare lastra"), (2, "Calcetto", "2026-10-08T20:00", None)]
    finally:
        del azioni.TOUCHES["prova_agenda"]


def test_values_and_since(home):
    state = {"v": [40, False]}
    reg = {"volume": azioni.value("volume", lambda: list(state["v"]), lambda v: state.update(v=v))}
    tool = Tool("set_volume", "Volume.", params(), lambda level="": state.update(v=[80, False]) or "Volume alzato.")
    azioni.TOUCHES["set_volume"] = ["volume"]
    real = azioni.parts
    azioni.parts = lambda: reg
    try:
        wrapped = azioni.wrap(tool)
        assert wrapped.func(level="80") == "Volume alzato." and state["v"] == [80, False]
        assert "Volume cambiato: 80" in azioni.describe()
        assert azioni.undo_since(azioni.since("stamattina")) == "Ho annullato 1 azione." and state["v"] == [40, False]
    finally:
        azioni.parts = real
    now = datetime(2026, 10, 6, 15, 30)
    assert azioni.since("nell'ultima ora", now) == datetime(2026, 10, 6, 14, 30).timestamp()
    assert azioni.since("da stamattina", now) == datetime(2026, 10, 6).timestamp()
    assert azioni.since("ultimi 20 minuti", now) == datetime(2026, 10, 6, 15, 10).timestamp()
    assert azioni.since("ieri sera", now) == datetime(2026, 10, 5, 18).timestamp()
    assert azioni.since("boh", now) is None


def test_personalization_is_undone_with_codice(home, monkeypatch):
    from aios_copilot import codice

    history = [{"id": "a1"}]
    undone = []
    monkeypatch.setattr(codice, "history", lambda: list(history))
    monkeypatch.setattr(codice, "undo", lambda change: undone.append(change) or (True, "orologio rotondo"))
    monkeypatch.setattr(azioni, "_restart_shell", lambda: None)
    azioni.record("customize_system", {"richiesta": "orologio rotondo"}, lambda: history.insert(0, {"id": "b2"}) or "Fatto.")
    assert azioni.pending()[0]["speciale"] == {"tipo": "personalizzazione", "id": "b2"}
    assert azioni.undo_last().startswith("Annullato") and undone == ["b2"]


def test_router_and_tools(home):
    r = ActionsRouter()
    assert r.match("annulla l'ultima cosa che hai fatto").tool == "undo_last_action"
    assert r.match("Annulla quello che hai appena fatto").args == {"quante": "1"}
    assert r.match("annulla le ultime tre cose").args == {"quante": "tre"}
    assert r.match("rimetti tutto com'era stamattina").args == {"quando": "stamattina"}
    assert r.match("annulla tutto quello che hai fatto nell'ultima ora").args == {"quando": "nell'ultima ora"}
    assert r.match("cosa hai cambiato oggi?").tool == "list_actions"
    assert r.match("annulla l'appuntamento dal dentista") is None  # è l'agenda, non il registro
    tools = {t.name: t for t in make_tools()}
    assert tools["undo_actions_since"].requires_confirmation
    assert tools["undo_last_action"].func() == "Non ho fatto niente da annullare."
    assert "Da quando?" in tools["undo_actions_since"].func(quando="boh")


def test_registry_parts_and_wrapping(home):
    reg = azioni.parts()
    assert set(n for names in azioni.TOUCHES.values() for n in names) <= set(reg)
    plain = Tool("search_web", "Cerca.", params(), lambda q="": "x")
    assert azioni.wrap(plain) is plain
    wrapped = azioni.wrap(Tool("add_widget", "Widget.", params(), lambda tipo="": "ok"))
    assert wrapped.name == "add_widget" and wrapped.func(tipo="nota") == "ok"


def test_real_agenda_roundtrip(home):
    from aios_copilot.agenda import Agenda

    a = Agenda()
    azioni.record("add_event", {"title": "Dentista"}, lambda: a.add_event("Dentista", datetime(2026, 10, 7, 17, 30)) and "Fatto.")
    assert [e["descrizione"] for e in azioni.pending()] == ["Appuntamento aggiunto: Dentista"]
    assert azioni.undo_last().startswith("Annullato")
    assert not a.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert json.loads(azioni.path().read_text().splitlines()[0])["annullata"]
