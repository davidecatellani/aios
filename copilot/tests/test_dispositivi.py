from aios_copilot import dispositivi as D
from aios_copilot import hyprconf
from aios_copilot.tools.display import DisplayRouter


def test_keys_and_bind_lines():
    assert D.normalize_keys("super + m") == "SUPER+M" and D.normalize_keys("Alt+Ctrl+T") == "CTRL+ALT+T"
    assert D.normalize_keys("m") is None and D.normalize_keys("super+a+b") is None
    assert D.bind_line({"tasti": "SUPER+M", "chiedi": "metti la musica 'rilassante'"}) == \
        "bind = SUPER, M, exec, aios-shell --chiedi 'metti la musica '\"'\"'rilassante'\"'\"''"
    assert D.bind_line({"tasti": "SUPER+F", "app": "org.mozilla.firefox"}) == "bind = SUPER, F, exec, gtk-launch org.mozilla.firefox"
    assert D.bind_line({"tasti": "SUPER+F", "app": "x; rm -rf ~"}) is None
    assert D.bind_line({"tasti": "SUPER+V", "app": "x"}) is None  # riservata ad AIOS


def test_apply_and_shortcuts(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    calls = []
    hypr = lambda c: calls.append(c) or (0, "ok")  # noqa: E731
    conf, _ = D.apply({"velocita": 5, "accelerazione": False, "tocco_clic": False, "ripetizione_ritardo": 10}, hypr)
    assert conf["velocita"] == 1.0 and conf["ripetizione_ritardo"] == 150
    assert ["keyword", "input:accel_profile", "flat"] in calls and ["keyword", "input:touchpad:tap-to-click", "0"] in calls
    ok, msg = D.add_shortcut("Super+M", ask="metti la musica rilassante", hypr=hypr)
    assert ok and msg == "Super+M chiede a Nova «metti la musica rilassante»."
    assert calls[-1][:2] == ["keyword", "bind"]
    assert not D.add_shortcut("Super+V", app="firefox", hypr=hypr)[0]
    text = hyprconf.conf_path().read_text()
    assert "accel_profile = flat" in text and "tap-to-click = false" in text and "--chiedi" in text
    assert D.remove_shortcut("super+m", hypr) and calls[-1] == ["keyword", "unbind", "SUPER, M"]
    assert "--chiedi" not in hyprconf.conf_path().read_text()


def test_router():
    r = DisplayRouter()
    assert r.match("crea una scorciatoia super+m per metti la musica rilassante").args == \
        {"tasti": "super+m", "richiesta": "metti la musica rilassante"}
    assert r.match("crea una scorciatoia super+f che apre firefox").args == {"tasti": "super+f", "programma": "firefox"}
    assert r.match("mouse più veloce").args == {"opzione": "velocità", "valore": "più"}
