import json

from aios_copilot import keyboard
from aios_copilot import shell as sh

CLIENTS = [
    {"address": "0xa1", "class": "org.mozilla.firefox", "title": "Notizie", "workspace": {"id": 1}, "floating": False, "focusHistoryID": 0},
    {"address": "0xb2", "class": "org.gnome.TextEditor", "title": "nota.txt", "workspace": {"id": 1}, "floating": False, "focusHistoryID": 1},
    {"address": "0xc3", "class": "org.gnome.TextEditor", "title": "Salva", "workspace": {"id": 1}, "floating": True, "focusHistoryID": 2},
]


def fake(calls):
    def run(cmd):
        calls.append(cmd)
        return (0, json.dumps(CLIENTS)) if cmd[:2] == ["hyprctl", "clients"] else (0, "ok")
    return run


def test_windows_with_hyprland(monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    calls = []
    ws = sh.open_windows(fake(calls))
    firefox = next(w for w in ws if w["app_id"] == "org.mozilla.firefox")
    assert firefox["title"] == "Notizie" and "indirizzo" in firefox and "attiva" in firefox
    assert sh.focus_window("", fake(calls), address="0xa1") and calls[-1][-1] == "address:0xa1"  # quella finestra
    assert not sh.close_window("x", fake(calls), address="0xa1; rm -rf") or calls[-1][-1] != "address:0xa1; rm -rf"
    assert sh.focus_window("org.mozilla.firefox", fake(calls))
    assert calls[-1] == ["hyprctl", "dispatch", "focuswindow", r"class:^(org\.mozilla\.firefox)$"]
    assert not sh.focus_window("org.videolan.VLC", fake(calls))  # non aperto: si avvia
    sh.minimize_all(fake(calls))
    assert calls[-1] == ["hyprctl", "dispatch", "workspace", "empty"]


def test_new_window_gets_own_workspace(monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    calls = []
    assert sh.own_workspace("b2", fake(calls))
    assert calls[-1] == ["hyprctl", "dispatch", "movetoworkspace", "empty,address:0xb2"]
    assert not sh.own_workspace("c3", fake(calls))  # il dialogo resta sopra il suo programma


def test_keyboard_with_hyprland(monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "x")
    calls = []
    assert keyboard.set_layout("inglese", run=lambda cmd: calls.append(cmd) or 0) == "Tastiera inglese (regno unito) attiva."
    assert calls == [["hyprctl", "keyword", "input:kb_layout", "gb"]]
    assert keyboard.current() == "gb"
