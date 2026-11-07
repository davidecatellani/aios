import os
from datetime import datetime, timedelta

import pytest

from aios_copilot.agenda import Agenda, find_deadlines, occurrences, run_service
from aios_copilot.agent import Agent
from aios_copilot.fastpath import Intent
from aios_copilot.tools import agenda as agenda_tools
from aios_copilot.when import describe, parse_when

NOW = datetime(2025, 10, 2, 10, 0)  # giovedì


@pytest.mark.parametrize(
    "text, at, all_day, repeat, rest",
    [
        ("ricordami di chiamare la mamma domani alle 18", datetime(2025, 10, 3, 18), False, "", "chiamare la mamma"),
        ("dentista giovedì alle 3 del pomeriggio", datetime(2025, 10, 9, 15), False, "", "dentista"),
        ("tra 20 minuti ricordami di togliere la pasta", datetime(2025, 10, 2, 10, 20), False, "", "togliere la pasta"),
        ("fra due ore ricordami di uscire", datetime(2025, 10, 2, 12), False, "", "uscire"),
        ("riunione il 12 novembre alle 10 e mezza", datetime(2025, 11, 12, 10, 30), False, "", "riunione"),
        ("prendere la pillola ogni giorno alle 8", datetime(2025, 10, 3, 8), False, "daily", "prendere la pillola"),
        ("comprare il latte", None, False, "", "comprare il latte"),
        ("cena da Luca sabato sera", datetime(2025, 10, 4, 20), False, "", "cena da Luca"),
        ("pagare la bolletta entro il 15/10", datetime(2025, 10, 15, 9), True, "", "pagare la bolletta"),
        ("compleanno di Anna il primo dicembre", datetime(2025, 12, 1, 9), True, "", "compleanno di Anna"),
        ("ricordami alle 9 di chiamare Giulia", datetime(2025, 10, 3, 9), False, "", "chiamare Giulia"),
        ("remind me to call John tomorrow at 5pm", datetime(2025, 10, 3, 17), False, "", "call John"),
        ("spazzatura tutti i martedì alle 21", datetime(2025, 10, 7, 21), False, "weekly", "spazzatura"),
        ("visita il 3/11/2025 alle 11:15", datetime(2025, 11, 3, 11, 15), False, "", "visita"),
        ("affitto il 1/3", datetime(2026, 3, 1, 9), True, "", "affitto"),  # marzo è già passato: anno prossimo
    ],
)
def test_parse_when(text, at, all_day, repeat, rest):
    w = parse_when(text, NOW)
    assert (w.at, w.all_day, w.repeat, w.rest) == (at, all_day, repeat, rest)


def test_describe():
    assert describe(datetime(2025, 10, 2, 18), now=NOW) == "oggi alle 18:00"
    assert describe(datetime(2025, 10, 3), True, now=NOW) == "domani"
    assert describe(datetime(2025, 11, 12, 10, 30), now=NOW) == "mercoledì 12 novembre alle 10:30"


def test_recurrences():
    start, end = datetime(2025, 1, 1), datetime(2025, 6, 1)
    monthly = list(occurrences(datetime(2025, 1, 31, 9), "monthly", start, end))
    assert [d.month for d in monthly] == [1, 3, 5]  # i mesi senza il 31 si saltano
    weekly = list(occurrences(datetime(2024, 12, 2, 21), "weekly", datetime(2025, 3, 1), datetime(2025, 3, 15)))
    assert [d.day for d in weekly] == [3, 10] and all(d.weekday() == 0 for d in weekly)
    assert list(occurrences(datetime(2025, 2, 1), "", start, end)) == [datetime(2025, 2, 1)]


@pytest.fixture
def clock():
    return [NOW]


@pytest.fixture
def agenda(tmp_path, clock):
    return Agenda(tmp_path / "agenda.db", clock=lambda: clock[0])


def test_store_and_permissions(agenda, tmp_path):
    agenda.add_event("Dentista", datetime(2025, 10, 2, 15))
    agenda.add_reminder("Comprare il latte", None)
    agenda.add_reminder("Pillola", datetime(2025, 10, 1, 8), repeat="daily")
    today = agenda.day(NOW.date())
    assert [i.title for i in today] == ["Pillola", "Dentista"]
    assert [i.title for i in agenda.todos()] == ["Comprare il latte"]
    assert oct(os.stat(tmp_path / "agenda.db").st_mode)[-3:] == "600"


def test_notifications_once_at_the_right_time(agenda, clock):
    agenda.add_event("Dentista", datetime(2025, 10, 3, 15), location="Via Roma 3")
    agenda.add_reminder("Togliere la pasta", datetime(2025, 10, 2, 10, 20))
    assert agenda.due_notifications() == []
    clock[0] = datetime(2025, 10, 2, 10, 20)
    [(key, _, text)] = agenda.due_notifications()
    assert text == "🔔 Togliere la pasta"
    agenda.mark_sent(key)
    assert agenda.due_notifications() == []  # mai due volte
    clock[0] = datetime(2025, 10, 2, 15, 0)
    assert [t for _, _, t in agenda.due_notifications()] == ["Domani alle 15:00: Dentista — Via Roma 3"]


def test_new_event_does_not_fire_already_past_alerts(agenda):
    agenda.add_event("Treno", datetime(2025, 10, 3, 9))  # "un giorno prima" sarebbe stato alle 9 di oggi
    assert agenda.due_notifications() == []


def test_missed_alerts_are_recovered_after_standby(agenda, clock):
    agenda.add_reminder("Chiamare Giulia", datetime(2025, 10, 2, 11))
    agenda.add_reminder("Vecchio", datetime(2025, 10, 2, 10, 5))
    clock[0] = datetime(2025, 10, 2, 13)  # il PC era in standby dalle 10:30 alle 13
    titles = {title for _, title, _ in agenda.due_notifications()}
    assert titles == {"Chiamare Giulia", "Vecchio"}
    clock[0] = datetime(2025, 10, 3, 13)  # oltre 6 ore dopo: non si disturba più
    assert agenda.due_notifications() == []


def test_service_sends_briefing_once_a_day(agenda, clock, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    sent = []
    clock[0] = datetime(2025, 10, 2, 7, 30)
    run_service(agenda, lambda t, b: sent.append(t), once=True)
    assert sent == []
    clock[0] = datetime(2025, 10, 2, 9, 15)  # accensione alle 9:15
    run_service(agenda, lambda t, b: sent.append(t), once=True)
    run_service(agenda, lambda t, b: sent.append(t), once=True)
    assert sent == ["Il tuo riepilogo"]


def test_briefing_text(agenda, clock):
    agenda.add_event("Riunione", datetime(2025, 10, 2, 11))
    agenda.add_event("Dentista", datetime(2025, 10, 3, 15))
    agenda.add_reminder("Bolletta", datetime(2025, 10, 1, 9))  # scaduta ieri
    agenda.add_reminder("Comprare il latte", None)
    agenda.suggest("Pagamento entro il 15/10", datetime(2025, 10, 15, 9), "/home/u/fattura.pdf")
    text = agenda.briefing("Davide", ["/home/u/Documenti/tesi.odt"])
    assert text.startswith("Buongiorno, Davide! Oggi è giovedì 2 ottobre.")
    for part in ["Riunione", "Rimasti indietro", "Bolletta", "Comprare il latte", "Domani: 15:00 Dentista",
                 "[1]", "fattura.pdf", "tesi.odt"]:
        assert part in text


def test_ics_round_trip(agenda, tmp_path, clock):
    agenda.add_event("Riunione, team", datetime(2025, 10, 6, 9, 30), location="Ufficio", repeat="weekly")
    agenda.add_event("Ferie", datetime(2025, 8, 10), all_day=True)
    other = Agenda(tmp_path / "other.db", clock=lambda: clock[0])
    assert other.import_ics(agenda.export_ics()) == 2
    items = other.find("riunione")
    assert items[0].title == "Riunione, team" and items[0].location == "Ufficio" and items[0].repeat == "weekly"


def test_deadlines_in_documents():
    text = ("Gentile cliente, la fattura n. 117 è da pagare entro il 15/11/2025.\n"
            "Abbiamo ricevuto il pagamento del 3 gennaio 2024.\n"  # passato: ignorato
            "La riunione di condominio si terrà il 20 ottobre alle 18.")
    found = find_deadlines(text, NOW)
    assert [(due.month, due.day) for _, due in found] == [(11, 15), (10, 20)]


# --- strumenti e frasi --------------------------------------------------------------


def tools_for(agenda):
    return {t.name: t for t in agenda_tools.make_tools(lambda: agenda, lambda: "Davide", lambda: [])}


def test_tools(agenda):
    t = tools_for(agenda)
    assert t["add_reminder"].func("chiamare la mamma", "2025-10-03T18:00") == "Fatto: ti ricorderò «chiamare la mamma» domani alle 18:00."
    assert "ogni giorno" in t["add_reminder"].func("pillola", "ogni giorno alle 8")
    assert "già passato" in t["add_reminder"].func("x", "2025-10-01T08:00")
    assert "Aggiunto alle cose da fare" in t["add_reminder"].func("comprare il latte")
    assert "In agenda: «Dentista»" in t["add_event"].func("Dentista", "2025-10-03T15:00")
    assert "Dentista" in t["list_agenda"].func("domani")
    assert "comprare il latte" in t["list_agenda"].func("promemoria")
    assert t["complete_reminder"].func("latte") == "Segnato come fatto: «comprare il latte». 👍"
    assert t["delete_agenda_item"].requires_confirmation
    t["add_reminder"].func("prenotare il dentista")
    assert t["delete_agenda_item"].func("dentista").startswith("Ho trovato più elementi")  # mai cancellare a caso


def test_router_end_to_end_without_llm(agenda):
    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM per le frasi d'agenda")

    agent = Agent(NoModel(), list(tools_for(agenda).values()), confirm=lambda *a, **k: True,
                  routers=[agenda_tools.AgendaRouter(clock=lambda: NOW)])
    assert agent.ask("Ricordami di chiamare la mamma domani alle 18") == "Fatto: ti ricorderò «chiamare la mamma» domani alle 18:00."
    assert agent.ask("ho la visita medica il 3 novembre alle 11").startswith("In agenda: «Visita medica» lunedì 3 novembre alle 11:00")
    assert "chiamare la mamma" in agent.ask("che impegni ho domani?")
    assert agent.ask("Buongiorno!").startswith("Buongiorno, Davide!")


@pytest.mark.parametrize("text", ["fissa una riunione con Marco", "togli il wifi", "metti la sveglia alle 7",
                                  "che tempo fa domani", "apri firefox"])
def test_router_leaves_other_requests(text):
    assert agenda_tools.AgendaRouter(clock=lambda: NOW).match(text) is None


def test_router_list_and_suggestions():
    r = agenda_tools.AgendaRouter(clock=lambda: NOW)
    assert r.match("i miei promemoria") == Intent("list_agenda", {"period": "promemoria"})
    assert r.match("che impegni ho questo weekend") == Intent("list_agenda", {"period": "weekend"})
    assert r.match("ignora la scadenza 3") == Intent("resolve_suggestion", {"number": "3", "accept": "no"})


def test_deadline_task_proposes_without_adding(agenda, tmp_path):
    from aios_copilot.fileindex import FileIndex
    from aios_copilot.learning import DeadlineTask

    home = tmp_path / "home"
    home.mkdir()
    (home / "fattura.txt").write_text("Fattura 117: importo da pagare entro il 15/11/2025.")
    index = FileIndex(tmp_path / "index.db", roots=[home], excluded=[])
    while index._step_one() and index.stats()["files"] == 0:
        pass
    task = DeadlineTask(index, agenda, tmp_path / "deadline.json")
    assert task.has_work()
    task.step(1)
    assert not task.has_work()  # checkpoint salvato: non si rilegge
    [(sid, title, due, source)] = agenda.pending_suggestions()
    assert due == datetime(2025, 11, 15, 9) and source.endswith("fattura.txt")
    assert agenda.between(NOW, NOW + timedelta(days=60)) == []  # solo proposta, niente in agenda
    agenda.resolve_suggestion(sid, True)
    assert [i.title for i in agenda.between(NOW, NOW + timedelta(days=60))] == [title]
