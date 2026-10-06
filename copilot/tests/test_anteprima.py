import json
from datetime import datetime

from aios_copilot import anteprima, cloud
from aios_copilot.nucleo import PROMPT_SCHERMATA, SYSTEM_SCHERMATA, Nucleo


def test_clock_verdict_on_twelve_hours():
    now = datetime(2026, 10, 6, 22, 10)
    assert "giusto" in anteprima.clock_verdict("10:10", now)
    assert "giusto" in anteprima.clock_verdict("10:12", now)  # qualche minuto di margine
    assert "sbagliate" in anteprima.clock_verdict("2:50", now)  # ore × 6 invece di × 30
    assert "giusto" in anteprima.clock_verdict("12:01", datetime(2026, 10, 6, 23, 59))  # a cavallo delle 12
    assert "non si legge" in anteprima.clock_verdict("dieci", now)


def test_fake_person_never_reaches_the_user_or_internet(tmp_path):
    env = anteprima.fake_env(tmp_path / "giulia")
    assert all(env[k].startswith(str(tmp_path)) for k in ("HOME", "AIOS_CASA", "XDG_CONFIG_HOME", "XDG_DATA_HOME"))
    assert env["https_proxy"] == anteprima.BLOCKED and "127.0.0.1" in env["no_proxy"]
    png = anteprima._gray_png(8)
    assert png.startswith(b"\x89PNG") and len(png) > 40
    weather = json.loads(anteprima._fake_weather("x"))
    assert len(weather["daily"]["time"]) == 5 and weather["current"]["weather_code"] == 2


def test_nucleo_screen_check_needs_the_adapter():
    calls = []

    def post(url, payload, timeout):
        calls.append(payload)
        return {"choices": [{"message": {"content": json.dumps(
            {"orologio": "10:10", "problemi": [{"tipo": "tagliato", "testo": "Il tuo riep"}]})}}]}

    without = Nucleo("http://x", post=post, get=lambda url, t: [{"id": 0, "path": "/n/documenti.gguf"}])
    assert without.check_screen(b"\x89PNGxx") is None and not calls
    n = Nucleo("http://x", post=post, get=lambda url, t: [{"id": 0, "path": "/n/documenti.gguf"},
                                                          {"id": 1, "path": "/n/schermate.gguf"}])
    data = n.check_screen(b"\x89PNGxx")
    assert data["orologio"] == "10:10" and data["problemi"][0]["tipo"] == "tagliato"
    p = calls[0]
    assert p["messages"][0]["content"] == SYSTEM_SCHERMATA and p["messages"][1]["content"][1]["text"] == PROMPT_SCHERMATA
    assert {x["id"]: x["scale"] for x in p["lora"]} == {0: 0.0, 1: 1.0}


def test_nucleo_answer_becomes_a_verdict(monkeypatch, tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG")
    monkeypatch.setattr(Nucleo, "check_screen", lambda self, image: {"orologio": "2:50", "problemi": [
        {"tipo": "sovrapposti", "testo": "Buongiorno"}]})
    monkeypatch.setattr(anteprima, "_cloud_look", lambda image, prompt: "")
    text = anteprima.look(img, "orologio rotondo", now=datetime(2026, 10, 6, 10, 10))
    assert "sbagliate" in text and "sovrapposti: «Buongiorno»" in text


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
