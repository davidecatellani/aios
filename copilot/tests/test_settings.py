from pathlib import Path

from aios_copilot.tools import settings
from aios_copilot.tools.base import Runner


class FakeRunner(Runner):
    def __init__(self, programs, codes=None):
        super().__init__(which=lambda p: p if p in programs else None)
        self.codes = codes or {}
        self.ran, self.spawned = [], []

    def run(self, cmd):
        self.ran.append(cmd)
        return self.codes.get(tuple(cmd[:2]), 0), ""

    def spawn(self, cmd):
        self.spawned.append(cmd)


def tools(runner, **kw):
    return {t.name: t.func for t in settings.make_tools(runner, **kw)}


def test_volume_prefers_pipewire_then_pulseaudio():
    r = FakeRunner({"wpctl", "pactl"})
    assert tools(r)["set_volume"]("up") == "Volume alzato."
    assert r.ran[-1][0] == "wpctl"
    r = FakeRunner({"pactl"})
    tools(r)["set_volume"]("mute")
    assert r.ran[-1] == ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"]
    assert "Nessun controllo audio" in tools(FakeRunner(set()))["set_volume"]("up")
    assert "non valida" in tools(r)["set_volume"]("rm -rf")


def test_theme_gnome_or_kde():
    r = FakeRunner({"plasma-apply-colorscheme"})
    assert tools(r)["set_theme"]("dark") == "Tema scuro attivato."
    assert r.ran[-1] == ["plasma-apply-colorscheme", "BreezeDark"]


def test_radio_and_failure_reporting():
    r = FakeRunner({"nmcli"}, codes={("nmcli", "radio"): 1})
    assert "Non ci sono riuscito" in tools(r)["set_radio"]("wifi", "off")
    r = FakeRunner({"rfkill"})
    assert tools(r)["set_radio"]("bluetooth", "on") == "Bluetooth attivato."
    assert r.ran[-1] == ["rfkill", "unblock", "bluetooth"]


def test_play_opens_music_app_when_nothing_is_playing():
    r = FakeRunner({"playerctl", "gtk-launch"}, codes={("playerctl", "play"): 1})
    t = tools(r, find_desktop=lambda name: Path("elisa.desktop") if name == "elisa" else None)
    assert "apro elisa" in t["media_control"]("play")
    assert r.spawned == [["gtk-launch", "elisa"]]


def test_power_requires_confirmation():
    by_name = {t.name: t for t in settings.make_tools(FakeRunner({"systemctl"}))}
    assert by_name["power"].requires_confirmation
    assert not by_name["set_volume"].requires_confirmation
