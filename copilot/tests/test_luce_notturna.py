from datetime import date, datetime

from aios_copilot import luce_notturna as LN
from aios_copilot.tools.display import DisplayRouter, make_tools

ROMA = lambda: (41.9, 12.5)  # noqa: E731


def test_sun_times_rome():
    rise, sset = LN.sun_times(date(2026, 10, 5), 41.9, 12.5, 2)
    assert (rise.hour, sset.hour) == (7, 18) and 5 <= rise.minute <= 15 and 40 <= sset.minute <= 52


def test_target_with_hours_and_ramp():
    c = dict(LN.DEFAULTS, attiva=True, modo="orari")
    at = lambda h: LN.target(datetime.fromisoformat(f"2026-10-05T{h}"), c)  # noqa: E731
    assert at("12:00") == 6500 and at("20:29") == 6500 and at("21:00") == 4000 and at("03:00") == 4000
    assert 4000 < at("20:45") < 6500 and 4000 < at("06:45") < 6500 and at("07:00") == 6500
    assert LN.target(datetime(2026, 10, 5, 23), dict(c, attiva=False)) == 6500
    assert LN.target(datetime(2026, 10, 5, 12), dict(c, attiva=False, fino_a="2026-10-05T13:00")) == 4000


def test_settings_and_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    tool = next(t for t in make_tools() if t.name == "night_light")
    assert "attiva" in tool.func("accendi") and LN.settings()["attiva"]
    tool.func("più calda")
    assert LN.settings()["temperatura"] == 3500
    assert "adesso" in tool.func("adesso") and LN.settings()["fino_a"]
    assert tool.func("spegni").startswith("Luce notturna spenta") and not LN.settings()["attiva"]
    assert LN.save({"temperatura": 99999})["temperatura"] == 5500


def test_applier_uses_hyprsunset():
    calls, spawned = [], []

    class P:
        def poll(self):
            return None

        def terminate(self):
            calls.append("stop")

        def wait(self, t):
            pass

    a = LN.Applier(run=lambda cmd: calls.append(cmd) or 0, spawn=lambda cmd: spawned.append(cmd) or P(),
                   which=lambda n: "/usr/bin/" + n if n == "hyprsunset" else None)
    a.apply(4500)
    a.apply(4000)
    a.apply(6500)
    assert spawned == [["hyprsunset", "-t", "4500"]]
    assert ["hyprctl", "hyprsunset", "temperature", "4000"] in calls and ["hyprctl", "hyprsunset", "identity"] in calls


def test_router():
    r = DisplayRouter()
    assert r.match("accendi la luce notturna").args == {"stato": "accendi"}
    assert r.match("attiva la luce notturna adesso").args == {"stato": "adesso"}
    assert r.match("spegni la luce notturna").args == {"stato": "spegni"}
    assert r.match("schermo più caldo").args == {"stato": "più calda"}
