"""La shell di AIOS: carte della giornata, app, finestre, sicurezza della pagina."""

import json
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aios_copilot import shell
from aios_copilot.localapp import serve

NOW = datetime(2026, 10, 3, 9, 12)


def desktop(d, name, **kw):
    lines = ["[Desktop Entry]", "Type=Application"] + [f"{k}={v}" for k, v in kw.items()]
    (d / f"{name}.desktop").write_text("\n".join(lines))


def test_installed_apps_and_dock(tmp_path):
    d = tmp_path / "applications"
    d.mkdir()
    desktop(d, "org.mozilla.firefox", Name="Firefox", **{"Name[it]": "Firefox", "Exec": "firefox %u", "Icon": "firefox"})
    desktop(d, "org.gnome.Nautilus", Name="Files", **{"Name[it]": "File", "Exec": "nautilus", "Icon": "org.gnome.Nautilus"})
    desktop(d, "nascosta", Name="X", Exec="x", NoDisplay="true")
    desktop(d, "solo-kde", Name="K", Exec="k", OnlyShowIn="KDE;")
    desktop(d, "org.aios.Copilot", Name="Nova", Exec="aios-copilot")
    desktop(d, "giochino", Name="Giochino", Exec="flatpak run com.usebottles.bottles -b Giochino")
    apps = shell.installed_apps([d])
    # le app di sistema di GNOME non si vedono: File, Foto, Impostazioni… sono le app HTML di AIOS
    assert set(apps) == {"org.mozilla.firefox", "giochino"} and apps["giochino"].windows
    assert [a["label"] for a in shell.dock_apps(apps)] == ["File", "Internet", "Foto", "Musica", "Video", "Note",
                                                          "Impostazioni"]


def test_icon_lookup_refuses_paths(tmp_path):
    base = tmp_path / "icons" / "hicolor" / "scalable" / "apps"
    base.mkdir(parents=True)
    (base / "firefox.svg").write_text("<svg/>")
    assert shell.icon_path("firefox", [tmp_path / "icons"]) == base / "firefox.svg"
    assert shell.icon_path("../../etc/passwd", [tmp_path / "icons"]) is None
    assert shell.icon_path("/etc/passwd") is None  # solo .png/.svg


class FakeAgenda:
    class I:
        def __init__(self, title, at, all_day=False):
            self.title, self.at, self.all_day = title, at, all_day

    def day(self, d):
        return [self.I("palestra", NOW.replace(hour=18, minute=30)), self.I("colazione", NOW.replace(hour=7))]

    def overdue(self):
        return [self.I("bolletta della luce", NOW - timedelta(days=1))]

    def pending_suggestions(self):
        return [(3, "Pagamento <fattura>", NOW + timedelta(days=12), "/home/u/fattura-idraulico.pdf")]


def test_day_cards_from_agenda():
    cards = shell.day_cards(FakeAgenda(), NOW, ["/home/u/tesi.odt"])
    summary, deadline, resume = cards
    assert "<b>1 impegno</b>" in summary["testo"] and "palestra alle 18:30" in summary["testo"]
    assert "bolletta della luce" in summary["testo"] and "tesi.odt" in summary["nota"]
    assert deadline["titolo"] == "Pagamento &lt;fattura&gt;"  # niente HTML dai documenti
    assert deadline["azioni"][0]["chiedi"] == "aggiungi la scadenza 3"
    assert resume["azioni"][0]["apri"] == "/home/u/tesi.odt"
    assert shell.greeting("Davide", NOW)["saluto"] == "Buongiorno, Davide."
    assert shell.greeting("", NOW.replace(hour=21))["saluto"] == "Buonasera."


def test_windows_via_wlrctl():
    ran = []

    def run(cmd):
        ran.append(cmd)
        if cmd[:3] == ["wlrctl", "toplevel", "list"]:
            return 0, "org.mozilla.firefox: Notizie — Firefox\norg.aios.Shell: AIOS\nfoot: foot\n"
        return 0, ""

    assert [w["app_id"] for w in shell.open_windows(run)] == ["org.mozilla.firefox", "foot"]
    shell.minimize_all(run)
    assert ["wlrctl", "toplevel", "minimize", "app_id:foot"] in ran
    assert not shell.focus_window("x; rm -rf /", run)


def test_status_bar():
    class R:
        level, charging = 84, False

    st = shell.status_bar(lambda: R(), lambda cmd: (0, "wifi:connected:Casa\nloopback:connected (externally):lo"),
                          lambda: ["Pixel 8"])
    assert st == {"ai": "AI in locale", "batteria": 84, "in_carica": False, "rete": "📶", "rete_nome": "Casa", "rete_tipo": "wifi",
                  "dispositivi": ["Pixel 8"]}
    assert "rete" not in shell.status_bar(lambda: R(), lambda cmd: (0, "wifi:disconnected:"), lambda: [])


def call(base, token, path, data=None):
    req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"X-AIOS-Token": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_home_api_and_safety(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))
    app = shell.ShellApp(None, clock=lambda: NOW, agenda=FakeAgenda, apps=lambda: {},
                         status=lambda: {"ai": "AI in locale"}, recent=lambda: [])
    server, url = serve(app)
    base, token = url.split("/#")[0], url.split("#t=")[1]
    try:
        home = call(base, token, "/api/casa")
        assert home["saluto"] == "Buongiorno." and home["carte"][0]["tipo"] == "riepilogo" and home["esempi"]
        with pytest.raises(urllib.error.HTTPError):
            call(base, "sbagliato", "/api/casa")
        with pytest.raises(urllib.error.HTTPError) as err:
            call(base, token, "/api/apri", {"percorso": "/etc/passwd"})  # solo i file dell'utente
        assert err.value.code == 403
        assert urllib.request.urlopen(base + "/", timeout=10).read().startswith(b"<!doctype html>")
    finally:
        server.shutdown()


def test_voice_goes_to_the_shell_in_aios(monkeypatch):
    from aios_copilot import voice

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "AIOS")
    monkeypatch.setattr(voice.shutil, "which", lambda p: f"/usr/bin/{p}")
    ran = []
    voice.deliver("alza il volume", run=lambda cmd, **kw: ran.append(cmd))
    assert ran == [["/usr/bin/aios-shell", "--voce", "alza il volume"]]
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    voice.deliver("ciao", run=lambda cmd, **kw: ran.append(cmd))
    assert ran[-1][0] == "/usr/bin/aios-copilot"


def test_power_profile_follows_the_plug():
    assert shell.power_profile_for(False, 80) == "power-saver"
    assert shell.power_profile_for(True, 80) == "balanced"
    assert shell.power_profile_for(False, None) is None  # PC fisso: non si tocca


def test_update_cards():
    from aios_copilot import updates

    updates.save_state({"pronto": {"versione": "2026.10.05.21", "sicurezza": False, "quando": 2000},
                        "app_aggiornate": {"quando": 5000, "nomi": ["Firefox", "VLC"]}})
    cards = shell.update_cards(now=6000, booted=1000)
    assert [c["titolo"] for c in cards] == ["Nuova versione di AIOS pronta", "App aggiornate"]
    assert cards[0]["azioni"][0]["chiedi"] == "riavvia per aggiornare"
    # dopo il riavvio l'aggiornamento è applicato: la carta sparisce
    assert [c["titolo"] for c in shell.update_cards(now=6000, booted=3000)] == ["App aggiornate"]
