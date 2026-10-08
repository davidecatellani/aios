import base64
import re

from aios_copilot.shell import apps
from aios_copilot.tools.settings import ScreenshotRouter

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class App:
    def __init__(self):
        self.routes = {}

    def route(self, method, pattern, handler):
        self.routes[(method, pattern)] = handler

    def call(self, method, path, body):
        for (m, pattern), handler in self.routes.items():
            match = re.fullmatch(pattern, path)
            if m == method and match:
                return handler(match, body, {})
        raise KeyError(path)


def test_edited_image_is_saved_next_to_the_original(tmp_path, monkeypatch):
    monkeypatch.setattr(apps, "home", lambda: tmp_path)
    shot = tmp_path / "Immagini" / "Screenshot 1.png"
    shot.parent.mkdir()
    shot.write_bytes(b"originale")
    app = App()
    apps.register_apps(app, run=lambda cmd: (1, ""))
    data = "data:image/png;base64," + base64.b64encode(PNG).decode()
    code, r = app.call("POST", "/api/file/salva-immagine", {"p": str(shot), "dati": data, "copia": True})
    assert code == 200 and r["nome"] == "Screenshot 1 (modificato).png" and (shot.parent / r["nome"]).read_bytes() == PNG
    code, r = app.call("POST", "/api/file/salva-immagine", {"p": str(shot), "dati": data, "copia": True})
    assert r["nome"] == "Screenshot 1 (modificato 2).png" and shot.read_bytes() == b"originale"
    code, r = app.call("POST", "/api/file/salva-immagine", {"p": str(shot), "dati": data, "copia": False})
    assert shot.read_bytes() == PNG  # sovrascrivi
    assert app.call("POST", "/api/file/salva-immagine", {"p": str(shot), "dati": "bm9u", "copia": True})[0] == 400
    assert app.call("POST", "/api/file/salva-immagine", {"p": "/etc/passwd", "dati": data})[0] == 403


def test_voice_opens_the_editor():
    r = ScreenshotRouter()
    assert r.match("modifica lo screenshot").tool == "edit_image"
    assert r.match("ritaglia l'ultima schermata").tool == "edit_image"
    assert r.match("fai uno screenshot") is None
