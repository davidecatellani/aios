from aios_copilot import giochi
from aios_copilot.shell import DesktopApp


def test_recognizes_games():
    apps = {"net.supertux.SuperTux": DesktopApp("net.supertux.SuperTux", "SuperTux", "", "supertux2", game=True),
            "org.gnome.Calculator": DesktopApp("org.gnome.Calculator", "Calcolatrice", "", "gnome-calculator")}
    assert giochi.is_game("steam_app_1091500")
    assert giochi.is_game("eldenring.exe")
    assert giochi.is_game("SuperTux", apps) and giochi.is_game("net.supertux.SuperTux", apps)
    assert not giochi.is_game("firefox", apps) and not giochi.is_game("org.gnome.Calculator", apps)


def test_enter_and_leave(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    now = [0.0]
    calls = []
    mode = giochi.GameMode(unload=lambda: calls.append("scarica") or ["qwen3.5:2b"],
                           service=lambda a: calls.append(a) or True, notify=lambda t: calls.append("avviso"),
                           clock=lambda: now[0])
    mode.handle("openwindow", "abc,2,firefox,Firefox")
    assert not giochi.active()
    mode.handle("openwindow", "def,3,steam_app_570,Dota 2")
    assert giochi.active() and calls == ["scarica", "stop", "avviso"]
    mode.handle("closewindow", "def")
    now[0] = 10
    mode.tick()
    assert giochi.active()  # un caricamento tra due finestre non fa ripartire tutto
    now[0] = 40
    mode.tick()
    assert not giochi.active() and calls[-1] == "start"


def test_rest_work_pauses_while_playing(tmp_path, monkeypatch):
    from aios_copilot.learning import Scheduler

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    giochi.flag().write_text("steam_app_570")
    assert Scheduler([]).can_run() == (False, "gioco in corso")


def test_minimized_game_reload_is_decided(tmp_path, monkeypatch):
    """Gioco ridotto a icona: chi decide se ricaricare Nova è il modello decisionale; tornando nel gioco si libera."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    now = [0.0]
    calls, asked = [], []
    verdict = [False]
    mode = giochi.GameMode(unload=lambda: calls.append("scarica") or [], service=lambda a: calls.append(a) or True,
                           clock=lambda: now[0], free=lambda: 4.0,
                           decide=lambda game, away, free: asked.append((game, round(away))) or verdict[0])
    mode.handle("openwindow", "g1,3,steam_app_570,Dota 2")
    mode.handle("activewindowv2", "g1")
    mode.handle("activewindowv2", "f1")  # ridotto a icona / un altro programma
    now[0] = 30
    mode.tick()
    assert asked == []  # troppo presto per chiedere
    now[0] = 50
    mode.tick()
    assert asked == [("steam_app_570", 50)] and giochi.active()  # «aspetta»
    verdict[0] = True
    now[0] = 115
    mode.tick()
    assert not giochi.active() and calls[-1] == "start"  # «ricarica»: il gioco è ancora aperto
    mode.handle("activewindowv2", "g1")  # di nuovo nel gioco
    assert giochi.active() and calls[-2:] == ["scarica", "stop"]


def test_rule_without_decision_model():
    assert not giochi.decide_reload("x", 100, 4.0, ask=lambda s, q: (_ for _ in ()).throw(OSError()))
    assert giochi.decide_reload("x", 200, 4.0, ask=lambda s, q: None)
    assert not giochi.decide_reload("x", 200, 1.0, ask=lambda s, q: None)
    assert giochi.decide_reload("x", 50, 4.0, ask=lambda s, q: {"choice": "si", "confidence": 0.9})
