import json
import struct

from aios_copilot import sessione
from aios_copilot.tools.sessione import SessionRouter, make_tools


class App:
    def __init__(self, name):
        self.name = name


APPS = {"org.mozilla.firefox": App("Firefox"), "org.libreoffice.LibreOffice.writer": App("LibreOffice Writer"),
        "org.gnome.TextEditor": App("Editor di testo")}


def test_lz4_and_firefox_tabs(tmp_path):
    assert sessione.lz4_block(bytes([0x32]) + b"abc" + bytes([3, 0]), 9) == b"abcabcabc"
    data = json.dumps({"windows": [{"tabs": [
        {"entries": [{"url": "https://old.example", "title": "Vecchia"}, {"url": "https://www.bici.it/gravel", "title": "Bici"}],
         "index": 2},
        {"entries": [{"url": "about:newtab"}], "index": 1}]}]}).encode()
    block = bytes([0xF0, len(data) - 15]) + data  # solo letterali, lunghezza estesa
    prof = tmp_path / ".mozilla/firefox/x.default/sessionstore-backups"
    prof.mkdir(parents=True)
    (prof / "recovery.jsonlz4").write_bytes(b"mozLz40\0" + struct.pack("<I", len(data)) + block)
    assert sessione.firefox_tabs(tmp_path) == [{"titolo": "Bici", "url": "https://www.bici.it/gravel"}]


def test_file_from_window_title(tmp_path):
    doc = tmp_path / "Contratto affitto.odt"
    doc.write_text("x")
    assert sessione.file_for_title("Contratto affitto.odt — LibreOffice Writer", [doc]) == str(doc)
    assert sessione.file_for_title("Mozilla Firefox", [doc]) == ""


def test_last_session_ignores_the_shutdown(tmp_path):
    clock = [1000.0]
    s = sessione.Sessions(tmp_path, clock=lambda: clock[0])
    full = {"quando": 1000.0, "programmi": [{"app": "org.mozilla.firefox", "nome": "Firefox", "titolo": "", "file": ""},
                                            {"app": "org.gnome.TextEditor", "nome": "Editor di testo", "titolo": "", "file": ""}],
            "siti": []}
    assert s.record(full)
    assert not s.record(dict(full, quando=1030.0))  # niente di nuovo
    clock[0] = 2000.0
    s.record(dict(full, quando=1990.0, programmi=full["programmi"][:1]))  # chiusura durante lo spegnimento
    clock[0] = 2002.0
    s.record({"quando": 2002.0, "programmi": [], "siti": []})
    assert s.last_session()["programmi"] == full["programmi"]


def test_restore_here_and_from_another_device(tmp_path):
    s = sessione.Sessions(tmp_path, clock=lambda: 5000.0)
    doc = tmp_path / "tesi.odt"
    doc.write_text("x")
    s.record({"quando": 4000.0, "dispositivo": "qui", "siti": [{"titolo": "Bici", "url": "https://bici.it"}],
              "programmi": [{"app": "org.mozilla.firefox", "nome": "Firefox", "titolo": "", "file": ""},
                            {"app": "org.libreoffice.LibreOffice.writer", "nome": "LibreOffice Writer", "titolo": "",
                             "file": str(doc)}]})
    launched = []
    tools = {t.name: t for t in make_tools(sessions=lambda: s, apps=lambda: APPS, windows=lambda: [],
                                           launch=lambda cmd: launched.append(cmd) or True, boot=lambda: 4500.0)}
    out = tools["restore_session"].func("")
    assert "LibreOffice Writer con tesi.odt" in out
    assert ["gtk-launch", "org.mozilla.firefox"] in launched  # Firefox riapre da sé le sue schede
    assert s.offered(4500.0)
    s.store_remote("portatile", {"quando": 4800.0, "programmi": [], "siti": [{"titolo": "Meteo", "url": "https://meteo.it"}]})
    launched.clear()
    assert "1 sito" in tools["restore_session"].func("portatile")
    assert launched == [["gtk-launch", "org.mozilla.firefox", "https://meteo.it"]]
    assert "portatile" in tools["other_devices_session"].func()


def test_router():
    r = SessionRouter()
    assert r.match("riapri quello che avevo aperto").args == {"dispositivo": ""}
    assert r.match("riapri quello che avevo aperto sul portatile").args == {"dispositivo": "portatile"}
    assert r.match("riapri tutto sull'altro computer").args == {"dispositivo": "computer"}
    assert r.match("non riaprire la sessione").tool == "skip_session"
    assert r.match("cosa avevo aperto sull'altro computer?").tool == "other_devices_session"
    assert r.match("apri firefox") is None
