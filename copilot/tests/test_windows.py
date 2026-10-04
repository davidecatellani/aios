"""Programmi a finestra e app di AIOS comandati da Nova."""

from aios_copilot.agent import Agent
from aios_copilot.tools import windows as w

OPEN = [{"app_id": "org.mozilla.firefox", "title": "Notizie — Mozilla Firefox"},
        {"app_id": "com.spotify.Client", "title": "Spotify"}]


class NoModel:
    def chat(self, *a):
        raise AssertionError("niente LLM")


def agent(calls):
    tools = w.make_tools(windows=lambda: OPEN, focus=lambda a: calls.append(("davanti", a)) or True,
                         close=lambda a: calls.append(("chiudi", a)) or True,
                         shell=lambda *args: calls.append(("shell", *args)) or True)
    return Agent(NoModel(), tools, confirm=lambda *a, **k: True,
                 routers=[w.WindowsRouter(windows=lambda: OPEN, active=lambda: True)])


def test_windows_by_voice_without_model():
    calls = []
    a = agent(calls)
    assert a.ask("passa a Firefox").startswith("Ecco") and calls[-1] == ("davanti", "org.mozilla.firefox")
    assert a.ask("chiudi spotify").startswith("Chiuso") and calls[-1] == ("chiudi", "com.spotify.Client")
    assert "Spotify" in a.ask("quali programmi sono aperti?")
    assert a.ask("torna alla schermata") == "Ecco la schermata." and calls[-1] == ("shell", "--casa")
    a.ask("apri le impostazioni del wifi")
    assert calls[-1] == ("shell", "--vista", "impostazioni:wifi")
    a.ask("mostrami le foto")
    assert calls[-1] == ("shell", "--vista", "foto")


def test_router_leaves_the_rest_to_others():
    r = w.WindowsRouter(windows=lambda: OPEN, active=lambda: True)
    assert r.match("chiudi la porta del garage") is None  # non è un programma aperto
    assert r.match("apri firefox") is None  # aprire un programma lo fa il motore di intenti
    assert w.WindowsRouter(windows=lambda: OPEN, active=lambda: False).match("mostrami le foto") is None


def test_keyboard_layout_by_voice(tmp_path, monkeypatch):
    from aios_copilot import keyboard

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    env = tmp_path / "run" / "aios-labwc" / "environment"
    env.parent.mkdir(parents=True)
    env.write_text("XDG_CURRENT_DESKTOP=AIOS\nXKB_DEFAULT_LAYOUT=it\n")
    ran = []
    assert keyboard.set_layout("inglese", run=ran.append) == "Tastiera inglese (regno unito) attiva."
    assert "XKB_DEFAULT_LAYOUT=gb" in env.read_text() and "XKB_DEFAULT_LAYOUT=it" not in env.read_text()
    assert ran == [["labwc", "--reconfigure"]] and keyboard.current() == "gb"
    r = w.WindowsRouter(windows=lambda: [], active=lambda: True)
    assert r.match("metti la tastiera italiana").args == {"lingua": "italiana"}
    assert keyboard.set_layout("klingon").startswith("Non conosco")
