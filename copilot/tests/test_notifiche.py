from datetime import datetime

from aios_copilot import notifiche as N
from aios_copilot.tools.notifiche import NotificationsRouter, make_tools


def test_store_history_dedup_and_read(tmp_path):
    t = [1000.0]
    s = N.Store(tmp_path / "n.json", clock=lambda: t[0])
    s.add("Firefox", "Download", "50%", replaces=7)
    t[0] += 5
    s.add("Firefox", "Download", "100%", replaces=7)  # sostituisce quella di prima
    s.add("Telegram", "Giulia", "Ci vediamo alle 8?", desktop="org.telegram.desktop")
    assert [n["testo"] for n in s.load()] == ["Ci vediamo alle 8?", "100%"] and s.unread() == 2
    assert s.add("X", "", "") is None
    s.mark_read()
    assert s.unread() == 0
    assert s.clear("Firefox") == 1 and len(s.load()) == 1


def test_quiet_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    now = datetime(2026, 10, 5, 23, 30)
    conf = dict(N.DEFAULTS)
    assert N.quiet_now(now, conf, playing=lambda: False) == ""
    assert N.quiet_now(now, conf, playing=lambda: True) == "stai giocando"
    assert N.quiet_now(now, dict(conf, orari=True), playing=lambda: False) == "dalle 22:00 alle 07:00"
    assert N.quiet_now(datetime(2026, 10, 5, 12), dict(conf, orari=True), playing=lambda: False) == ""
    N.quiet_for(60, now)
    assert N.quiet_now(now, playing=lambda: False) == "fino alle 00:30"
    assert N.quiet_for(None, now)["fino_a"] == "2026-10-06T07:00"


def test_tools_and_router(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    s = N.Store(tmp_path / "n.json")
    s.add("Telegram", "Giulia", "Ci vediamo alle 8?")
    tools = {t.name: t for t in make_tools(s)}
    assert "Giulia" in tools["notifications_summary"].func() and s.unread() == 0
    assert "fino alle" in tools["do_not_disturb"].func("accendi", 30)
    assert tools["do_not_disturb"].func("spegni").startswith("«Non disturbare» spento")
    r = NotificationsRouter()
    assert r.match("cosa mi sono perso?").tool == "notifications_summary"
    assert r.match("non disturbarmi per un'ora").args == {"stato": "accendi", "minuti": 60}
    assert r.match("attiva il non disturbare per 30 minuti").args == {"stato": "accendi", "minuti": 30}
    assert r.match("attiva il non disturbare fino a domattina").args == {"stato": "domattina"}
    assert r.match("togli il non disturbare").args == {"stato": "spegni"}
