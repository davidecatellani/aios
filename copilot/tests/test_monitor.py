import json

from aios_copilot import hyprconf
from aios_copilot import monitor as M
from aios_copilot.tools.display import DisplayRouter

SCREENS = [
    {"name": "DP-1", "make": "AOC", "model": "24G2", "width": 1920, "height": 1080, "refreshRate": 60.0, "x": 0, "y": 0,
     "scale": 1.0, "transform": 0, "disabled": False, "vrr": False,
     "availableModes": ["1920x1080@144.00Hz", "1920x1080@59.94Hz", "1920x1080@60.00Hz", "1280x720@60.00Hz"]},
    {"name": "HDMI-A-1", "make": "ASUS", "model": "VG245", "width": 1920, "height": 1080, "refreshRate": 75.0, "x": 1920,
     "y": 0, "scale": 1.0, "transform": 0, "disabled": False, "availableModes": ["1920x1080@75.00Hz"]},
]


class Hypr:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        return (0, json.dumps(SCREENS)) if args[:2] == ["monitors", "all"] else (0, "ok")


class Timer:
    def __init__(self, secs, fn):
        self.fn, self.cancelled = fn, False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


def test_parse_and_rule():
    s = M.parse(json.dumps(SCREENS))
    assert s[0]["descrizione"] == "AOC 24G2" and s[0]["risoluzioni"]["1920x1080"] == [144.0, 60.0]
    assert M.rule(dict(s[0], frequenza=144.0, rotazione=1, vrr=True)) == "DP-1, 1920x1080@144, 0x0, 1, transform, 1, vrr, 1"
    assert M.best_rate_advice(s) == ["AOC 24G2 va a 60 Hz ma può arrivare a 144 Hz."]
    assert M.place(s[0], s[1], "sinistra") == (0, 0) and M.place(dict(s[0], scala=1.5), s[1], "sotto") == (1920, 1080)


def test_change_confirm_and_revert(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    h = Hypr()
    mon = M.Monitors(run=h, timer=Timer)
    ok, msg = mon.change("aoc", {"frequenza": 144})
    assert ok and "144 Hz" in msg and ["keyword", "monitor", "DP-1, 1920x1080@144, 0x0, 1"] in h.calls
    assert "monitor = DP-1, 1920x1080@144, 0x0, 1" in hyprconf.conf_path().read_text()
    assert mon.revert() and h.calls[-1] == ["keyword", "monitor", "DP-1, 1920x1080@60, 0x0, 1"]
    assert "1920x1080@60" in hyprconf.conf_path().read_text()
    mon.change("DP-1", {"scala": 1.25})
    assert mon.confirm() and not mon.revert()
    assert not mon.change("DP-1", {"frequenza": 240})[0]
    assert not mon.change("DP-1", {"risoluzione": "3840x2160"})[0]
    ok, _ = mon.change("HDMI-A-1", {"accanto": "sinistra", "di": "DP-1"})
    assert h.calls[-1] == ["keyword", "monitor", "HDMI-A-1, 1920x1080@75, -1920x0, 1"]


def test_router():
    r = DisplayRouter()
    assert r.match("metti lo schermo a 144 hz").args == {"frequenza": 144}
    assert r.match("porta il monitor al massimo").args == {"frequenza": -1}
    assert r.match("imposta la risoluzione a 2560x1440").args == {"risoluzione": "2560x1440"}
    assert r.match("ingrandisci tutto al 125%").args == {"scala": 125}
    assert r.match("che schermi ho?").tool == "list_displays"
