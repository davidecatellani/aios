from datetime import datetime, timedelta

import pytest

from aios_copilot.agent import Agent
from aios_copilot.energy import EnergyBrain, Reading, read_battery
from aios_copilot.mesh.service import MeshService
from aios_copilot.tools import energy as energy_tools


def history(days=10, charge_hour=23, wake_hour=7, drain=4.0, weekend_charge_hour=None):
    """Campioni ogni 30 minuti: in carica di notte, poi scarica di `drain` %/ora."""
    samples = []
    start = datetime(2026, 9, 1)  # martedì
    for d in range(days):
        day = start + timedelta(days=d)
        hour = weekend_charge_hour if weekend_charge_hour is not None and day.weekday() >= 5 else charge_hour
        t = day.replace(hour=wake_hour)
        level = 100.0
        while t.hour < hour or t.date() == day.date() and t.hour >= wake_hour and t < day.replace(hour=hour):
            samples.append([t.timestamp(), round(level), 0])
            t += timedelta(minutes=30)
            level = max(1, level - drain / 2)
        samples.append([day.replace(hour=hour).timestamp(), round(level), 1])
    return samples


def brain(tmp_path, at, samples=None, reading=None):
    b = EnergyBrain(tmp_path / "energia.json", clock=lambda: at.timestamp(), battery=lambda: reading)
    b.samples = samples if samples is not None else history()
    return b


def test_learns_drain_and_charging_habits(tmp_path):
    b = brain(tmp_path, datetime(2026, 9, 15, 21, 0))  # martedì sera
    assert b.drain_rate() == pytest.approx(4.0, abs=0.3)
    assert b.hours_to_charge(datetime(2026, 9, 15, 21, 0)) == (2.0, True)


def test_nova_decides_from_habits(tmp_path):
    evening = datetime(2026, 9, 15, 21, 0)
    d = brain(tmp_path, evening).decide(Reading(25, False))
    assert d.mode == "risparmio" and d.intervals["foto"] is None and d.intervals["bluetooth"] is None
    assert "tra circa 2 ore" in d.reason
    d = brain(tmp_path, datetime(2026, 9, 15, 22, 30)).decide(Reading(70, False))
    assert d.mode == "risparmio" and "ricarica è vicina" in d.reason  # anche con batteria abbondante: aspetta
    d = brain(tmp_path, datetime(2026, 9, 15, 9, 0)).decide(Reading(100, False))
    assert d.mode == "normale" and d.intervals["foto"] == 3600 and d.intervals["modelli"] is None
    d = brain(tmp_path, evening).decide(Reading(12, False))
    assert d.mode == "riserva" and all(v is None for v in d.intervals.values()) and "essenziale" in d.reason
    assert brain(tmp_path, evening).decide(Reading(12, True)).mode == "pieno"  # in carica: tutto
    assert brain(tmp_path, evening).decide(Reading(None, False)).mode == "pieno"  # PC fisso


def test_prudent_until_it_knows_you(tmp_path):
    d = brain(tmp_path, datetime(2026, 9, 15, 9, 0), samples=[]).decide(Reading(80, False))
    assert d.mode == "risparmio" and "non conosco ancora le tue abitudini" in d.reason


def test_weekends_are_learned_apart(tmp_path):
    samples = history(days=21, weekend_charge_hour=20)
    saturday = datetime(2026, 9, 26, 18, 0)
    assert brain(tmp_path, saturday, samples).hours_to_charge(saturday) == (2.0, True)
    tuesday = datetime(2026, 9, 22, 18, 0)
    assert brain(tmp_path, tuesday, samples).hours_to_charge(tuesday) == (5.0, True)


def test_user_choice_for_a_few_hours(tmp_path):
    now = datetime(2026, 9, 15, 9, 0)
    b = brain(tmp_path, now)
    assert b.choose("risparmio", 2).startswith("Risparmio la batteria fino alle 11:00")
    assert b.decide(Reading(100, False)).mode == "riserva"
    assert b.decide(Reading(100, True)).mode == "riserva"  # anche in carica, se l'ha chiesto
    b.choose("prestazioni", 1)
    assert b.decide(Reading(30, False)).mode == "normale"
    later = brain(tmp_path, now + timedelta(hours=2))
    later.override = b.override
    assert later.decide(Reading(100, False)).mode == "normale"  # scaduta: torna a decidere Nova
    assert "torno a decidere io" in b.choose("auto")


def test_reads_linux_battery(tmp_path):
    bat = tmp_path / "sys/class/power_supply/BAT0"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "capacity").write_text("57\n")
    (bat / "status").write_text("Discharging\n")
    assert read_battery(tmp_path) == Reading(57, False)
    ac = tmp_path / "sys/class/power_supply/AC"
    ac.mkdir()
    (ac / "type").write_text("Mains\n")
    (ac / "online").write_text("1\n")
    assert read_battery(tmp_path) == Reading(57, True)
    assert read_battery(tmp_path / "pc-fisso") == Reading(None, False)


def test_observe_samples_sparingly(tmp_path):
    t = [datetime(2026, 9, 15, 9, 0).timestamp()]
    b = EnergyBrain(tmp_path / "e.json", clock=lambda: t[0])
    b.observe(Reading(80, False))
    t[0] += 60
    b.observe(Reading(79, False))  # troppo presto: nessun campione
    b.observe(Reading(79, True))  # ma un cambio di carica si registra subito
    assert [s[1:] for s in b.samples] == [[80, 0], [79, 1]]
    assert EnergyBrain(tmp_path / "e.json").samples == b.samples  # salvato


def test_service_follows_novas_decision(tmp_path):
    class Phones:
        def nearby(self):
            return []

    class Server:
        running, pairing = False, type("P", (), {"code": "", "expires": 0})()

        def start(self, **k): pass

        def stop(self): pass

    class Calls:
        def incoming(self): return None

    b = brain(tmp_path, datetime(2026, 9, 15, 21, 0), reading=Reading(12, False))
    svc = MeshService(Phones(), Calls(), Server(), notify=lambda *a: None, clock=lambda: 10_000.0)
    svc.energy = b
    ran = []
    svc.sync_now = lambda: ran.append("sync") or []
    svc.tick()
    assert ran == []  # riserva: niente sincronizzazione in sottofondo
    b.battery = lambda: Reading(12, True)
    svc.tick()
    import time
    time.sleep(0.1)
    assert ran == ["sync"]  # in carica: riparte


def test_nova_explains_and_obeys():
    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM")

    class FakeBrain:
        def explain(self, activity=""):
            return f"spiegazione {activity}".strip()

        def choose(self, mode, hours):
            return f"scelta {mode} {hours}"

    agent = Agent(NoModel(), energy_tools.make_tools(lambda: FakeBrain()), confirm=lambda *a, **k: True,
                  routers=[energy_tools.EnergyRouter()])
    assert agent.ask("Nova, come gestisci la batteria?") == "spiegazione"
    assert agent.ask("perché non hai copiato le foto?") == "spiegazione foto"
    assert agent.ask("risparmia batteria per 2 ore") == "scelta risparmio 2.0"
    assert agent.ask("decidi tu") == "scelta auto 3.0"
