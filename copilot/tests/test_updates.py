import json
import time

import pytest

from aios_copilot import updates
from aios_copilot.agent import Agent
from aios_copilot.tools import updates as update_tools
from aios_copilot.tools.base import Runner


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


STATUS = {"deployments": [
    {"booted": False, "staged": True, "version": "42.20261010.0", "checksum": "bbb"},
    {"booted": True, "staged": False, "version": "42.20261001.0", "checksum": "aaa"},
    {"booted": False, "staged": False, "version": "42.20260920.0", "checksum": "zzz"},
]}
PREVIEW = """Note: --check and --preview may be unreliable.  See https://github.com/coreos/rpm-ostree/issues/1579
AvailableUpdate:
        Version: 42.20261010.0 (2026-10-10T00:00:00Z)
         Commit: bbb
   GPGSignature: Valid signature by 115DF9AEF857853EE8445D0A0727707EA15B79CC
  SecAdvisories: FEDORA-2026-1a2b3c  Important   openssl-3.2.4-1.fc42.x86_64
           Diff: 12 upgraded
    openssl 3.2.3-1.fc42 -> 3.2.4-1.fc42
"""


class FakeSystem(Runner):
    def __init__(self, tools=("rpm-ostree", "flatpak", "fwupdmgr"), preview=PREVIEW, status=STATUS, fail=()):
        super().__init__(which=lambda p: p if p in tools else None)
        self.preview, self.status, self.fail, self.ran = preview, status, fail, []

    def run(self, cmd):
        self.ran.append(cmd)
        joined = " ".join(cmd)
        if joined in self.fail:
            return 1, "errore di rete"
        if cmd[:3] == ["rpm-ostree", "upgrade", "--preview"]:
            return (77, "No updates available.") if not self.preview else (0, self.preview)
        if cmd[:2] == ["rpm-ostree", "status"]:
            return 0, json.dumps(self.status)
        if cmd[:2] == ["flatpak", "remote-ls"]:
            return 0, "Firefox\torg.mozilla.firefox\nGIMP\torg.gimp.GIMP\n"
        if cmd[:2] == ["fwupdmgr", "get-updates"]:
            return 0, json.dumps({"Devices": [{"Name": "UEFI dbx", "Releases": [{"Summary": "Security fix CVE-2026-1234"}]}]})
        if cmd[:2] == ["bootc", "upgrade"] and "--check" in cmd:
            return 0, "Update available for: quay.io/aios/aios:latest\n  Version: 2026.10.1\n"
        if cmd[:2] == ["bootc", "status"]:
            return 0, json.dumps({"status": {"booted": {"image": {"version": "2026.09.1"}}, "staged": None, "rollback": None}})
        return 0, ""


def test_check_finds_security_updates_and_apps():
    found = updates.Updates(FakeSystem()).check()
    system, apps, firmware = found
    assert system.kind == "sistema" and system.security and system.version == "42.20261010.0"
    assert "Important" in system.items[0]
    assert apps.items == ["Firefox", "GIMP"] and firmware.security and firmware.items == ["UEFI dbx"]
    assert updates.Updates(FakeSystem(preview="")).check()[0].kind == "app"  # sistema già aggiornato


def test_prepare_stages_without_rebooting():
    r = FakeSystem()
    report = updates.Updates(r).prepare()
    assert report[0] == "Sistema: pronto per il prossimo riavvio" and "Firefox" in report[1] and "firmware" in report[2].lower()
    assert ["rpm-ostree", "upgrade"] in r.ran and ["flatpak", "update", "--user", "-y", "--noninteractive"] in r.ran
    assert not any("reboot" in " ".join(c) for c in r.ran)  # mai riavviare da solo
    assert not any(c[0] == "fwupdmgr" and "update" in c[1:2] for c in r.ran)  # il firmware solo segnalato
    assert updates.load_state()["pronto"]["sicurezza"] is True
    failing = updates.Updates(FakeSystem(fail=("rpm-ostree upgrade",))).prepare()
    assert failing[0].startswith("Sistema: non riuscito")


def test_status_and_rollback():
    u = updates.Updates(FakeSystem())
    u.prepare()
    text = u.describe()
    assert "Sistema in uso: 42.20261001.0" in text and "🔒 Aggiornamento di sicurezza pronto (42.20261010.0)" in text
    assert "Versione precedente disponibile: 42.20260920.0" in text and "attivi" in text
    r = FakeSystem()
    assert "42.20260920.0" in updates.Updates(r).rollback() and r.ran[-1] == ["rpm-ostree", "rollback"]
    assert "non è immutabile" in updates.Updates(FakeSystem(tools=("flatpak",))).rollback()


def test_bootc_images():
    r = FakeSystem(tools=("bootc",))
    u = updates.Updates(r)
    assert u.check()[0].version == "2026.10.1"
    u.prepare()
    assert ["bootc", "upgrade"] in r.ran and "2026.09.1" in u.describe()


def test_idle_task_checks_twice_a_day_and_notifies():
    notes = []
    task = updates.UpdateTask(updates.Updates(FakeSystem()), notify=lambda t, b: notes.append(t))
    assert task.has_work()
    task.step(5)
    deadline = time.time() + 5
    while task.has_work() and time.time() < deadline:
        task.step(0.1)
    assert notes == ["Aggiornamento di sicurezza pronto", "App aggiornate", "Aggiornamento del firmware"]
    assert not task.has_work()
    updates.set_auto(False)
    state = updates.load_state()
    state["controllato"] = 0
    updates.save_state(state)
    assert not task.has_work()  # disattivati: niente controlli automatici


def test_health_check():
    assert updates.health_check(lambda c: (1, "active")) == []
    assert updates.health_check(lambda c: (1, "activating")) == []  # in attesa di internet: non è un guasto
    assert updates.health_check(lambda c: (0, "failed"))[0].startswith("Ollama")


def test_copilot_update_phrases(monkeypatch):
    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM")

    r, asked = FakeSystem(), []
    agent = Agent(NoModel(), update_tools.make_tools(r), confirm=lambda tool, args, **k: asked.append(tool.name) or True,
                  routers=[update_tools.UpdatesRouter()])
    monkeypatch.setattr("aios_copilot.agenda.notify", lambda title, body: None)
    assert "sottofondo" in agent.ask("aggiorna il sistema")
    updates.Updates._worker.join(10)
    assert "Sistema: pronto per il prossimo riavvio" in updates.load_state()["ultimo_esito"]
    assert "Aggiornamento di sicurezza pronto" in agent.ask("ci sono aggiornamenti?")
    assert agent.ask("riavvia per aggiornare").startswith("Riavvio") and r.ran[-1] == ["systemctl", "reboot"]
    assert "versione precedente" in agent.ask("torna alla versione precedente del sistema")
    assert asked == ["update_now", "restart_to_update", "rollback_system"]  # tutto ciò che cambia il sistema chiede conferma
    assert "disattivati" in agent.ask("disattiva gli aggiornamenti automatici")


def test_system_update_runs_in_background(tmp_path, monkeypatch):
    import threading

    from aios_copilot import updates as up_mod
    from aios_copilot.tools import updates as tools_mod

    monkeypatch.setenv("AIOS_AGGIORNAMENTI_DIR", str(tmp_path / "work"))
    gate, notes = threading.Event(), []

    class Slow(up_mod.Updates):
        def __init__(self):
            self.clock = lambda: 1000.0
            self.package = None

        def check(self, sources=("chiavetta", "github")):
            return [up_mod.Update("sistema", "AIOS 2026.10.05.21 (da GitHub, 8.0 GB)", False, "2026.10.05.21")]

        def prepare(self, found=None):
            gate.wait(5)
            return ["Sistema: pronto per il prossimo riavvio"]

    slow = Slow()
    monkeypatch.setattr("aios_copilot.agenda.notify", lambda title, body: notes.append(body))
    tools = {t.name: t for t in tools_mod.make_tools(updates=slow)}
    first = tools["update_now"].func()
    assert "sottofondo" in first and slow.busy()
    assert "in corso" in tools["update_now"].func()  # una seconda richiesta non ne fa partire un'altra
    gate.set()
    up_mod.Updates._worker.join(5)
    assert notes == ["Sistema: pronto per il prossimo riavvio"]
    assert up_mod.load_state()["ultimo_esito"] == ["Sistema: pronto per il prossimo riavvio"]
