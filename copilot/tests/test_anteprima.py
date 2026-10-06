import json
from datetime import datetime

from aios_copilot import anteprima, cloud
from aios_copilot.nucleo import SYSTEM_VERIFICA, VERIFY_SCHEMA, Nucleo, prompt_verifica


def test_fake_person_never_reaches_the_user_or_internet(tmp_path):
    env = anteprima.fake_env(tmp_path / "giulia")
    assert all(env[k].startswith(str(tmp_path)) for k in ("HOME", "AIOS_CASA", "XDG_CONFIG_HOME", "XDG_DATA_HOME"))
    assert env["https_proxy"] == anteprima.BLOCKED and "127.0.0.1" in env["no_proxy"]
    png = anteprima._gray_png(8)
    assert png.startswith(b"\x89PNG") and len(png) > 40
    weather = json.loads(anteprima._fake_weather("x"))
    assert len(weather["daily"]["time"]) == 5 and weather["current"]["weather_code"] == 2


def test_nucleo_check_change_needs_the_adapter():
    calls = []

    def post(url, payload, timeout):
        calls.append(payload)
        return {"choices": [{"message": {"content": json.dumps(
            {"fatto": False, "problemi": ["colore diverso da quello chiesto"]})}}]}

    without = Nucleo("http://x", post=post, get=lambda url, t: [{"id": 0, "path": "/n/documenti.gguf"}])
    assert without.check_change(b"\x89PNGa", b"\x89PNGb", "barra rossa") is None and not calls
    n = Nucleo("http://x", post=post, get=lambda url, t: [{"id": 0, "path": "/n/documenti.gguf"},
                                                          {"id": 1, "path": "/n/verifica.gguf"}])
    now = datetime(2026, 10, 6, 10, 10)
    data = n.check_change(b"\x89PNGa", b"\x89PNGb", "colora la barra di rosso", now)
    assert data["fatto"] is False and data["problemi"] == ["colore diverso da quello chiesto"]
    p = calls[0]
    content = p["messages"][1]["content"]
    assert p["messages"][0]["content"] == SYSTEM_VERIFICA
    assert [c["type"] for c in content] == ["image_url", "image_url", "text"]  # prima, dopo, richiesta
    assert content[2]["text"] == prompt_verifica("colora la barra di rosso", now) and "10:10" in content[2]["text"]
    assert {x["id"]: x["scale"] for x in p["lora"]} == {0: 0.0, 1: 1.0}
    assert p["response_format"]["json_schema"]["schema"] == VERIFY_SCHEMA


def test_verdict_from_the_nucleo(monkeypatch, tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG")
    monkeypatch.setattr(anteprima, "_cloud_look", lambda image, prompt: "")
    monkeypatch.setattr(Nucleo, "check_change", lambda self, a, b, r, now: {"fatto": False, "problemi": ["posizione sbagliata"]})
    text = anteprima.look(img, "sposta il meteo in basso", crops=(b"a", b"b", (0, 0, 1, 1)))
    assert text == "La modifica non sembra riuscita: posizione sbagliata."
    monkeypatch.setattr(Nucleo, "check_change", lambda self, a, b, r, now: {"fatto": True, "problemi": []})
    assert anteprima.look(img, "x", crops=(b"a", b"b", (0, 0, 1, 1))) == "La modifica sembra riuscita come chiesto."


def test_change_crops(tmp_path):
    import cv2
    import numpy as np

    a = np.full((400, 600, 3), 240, np.uint8)
    b = a.copy()
    cv2.rectangle(b, (300, 100), (360, 140), (20, 40, 200), -1)  # un riquadro diventato rosso
    enc = lambda x: cv2.imencode(".png", x)[1].tobytes()
    assert anteprima.change_crops(enc(a), enc(a)) is None  # niente di cambiato
    before, after, (x, y, w, h) = anteprima.change_crops(enc(a), enc(b))
    assert x <= 300 and y <= 100 and x + w >= 360 and y + h >= 140 and w < 300 and h < 300
    assert cv2.imdecode(np.frombuffer(after, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (h, w)


def test_cloud_sees_with_a_vision_model(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    sent = []

    def post(url, payload, key):
        sent.append(payload)
        return {"choices": [{"message": {"content": "Le lancette segnano le 10:10."}}], "usage": {"cost": 0.0004}}

    usage = cloud.Usage(tmp_path / "uso.json")
    assert cloud.see(b"\x89PNG", "che ora segna?", post=post, usage=usage, api_key="k") == "Le lancette segnano le 10:10."
    msg = sent[0]["messages"][0]["content"]
    assert sent[0]["model"] == cloud.VISION_MODEL and msg[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert usage.spent()[2] == 1
