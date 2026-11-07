import json

from aios_copilot import widget
from aios_copilot.tools.widget import WidgetRouter, make_tools

GEO = {"results": [{"name": "Bologna", "latitude": 44.49, "longitude": 11.34, "timezone": "Europe/Rome",
                    "admin1": "Emilia-Romagna", "country": "Italia"}]}
METEO = {"current": {"temperature_2m": 14.2, "apparent_temperature": 13.1, "weather_code": 2, "wind_speed_10m": 9.4,
                     "relative_humidity_2m": 70},
         "daily": {"time": ["2026-10-04", "2026-10-05"], "weather_code": [2, 61], "temperature_2m_max": [16.4, 13.0],
                   "temperature_2m_min": [9.1, 8.2], "precipitation_probability_max": [10, 80]}}


def fetch(url):
    if "geocoding" in url:
        return json.dumps(GEO if "Bologna" in url or "bologna" in url else {"results": []}).encode()
    if "forecast" in url:
        return json.dumps(METEO).encode()
    raise OSError("niente rete")


def test_add_render_and_remove_widgets():
    w, msg = widget.add("meteo", "Bologna", fetch=fetch)
    assert w["luogo"]["nome"] == "Bologna" and "aggiunto" in msg
    assert widget.add("meteo", "Bologna", fetch=fetch)[1].startswith("C'è già")
    widget.add("mappa", "Bologna", fetch=fetch)
    widget.add("nota", testo="Chiamare l'idraulico")
    assert widget.add("meteo", "Atlantide", fetch=fetch)[0] is None
    shown = widget.render(fetch)
    meteo = shown[0]
    assert meteo["temperatura"] == 14 and meteo["cielo"] == "parzialmente nuvoloso" and meteo["giorni"][1]["giorno"] == "lun"
    assert shown[1]["tipo"] == "mappa" and 0 < shown[1]["x"] < 2 ** 13 and shown[2]["testo"] == "Chiamare l'idraulico"
    assert widget.remove("la mappa di bologna") == "Fatto: tolto dalla home."
    assert [w["tipo"] for w in widget.load()] == ["meteo", "nota"]
    assert widget.remove("tutti i widget").startswith("Fatto")
    assert widget.load() == []


def test_without_internet_the_widget_stays():
    widget.save([{"id": "m", "tipo": "meteo", "luogo": {"nome": "Roma", "lat": 41.9, "lon": 12.5}}])
    widget._cache.clear()
    shown = widget.render(lambda url: (_ for _ in ()).throw(OSError("rete")))
    assert shown[0]["errore"] == "non disponibile adesso" and shown[0]["titolo"] == "Meteo · Roma"


def test_voice_commands():
    r = WidgetRouter()
    assert r.match("inserisci un widget per il meteo").args == {"tipo": "meteo", "luogo": "", "testo": ""}
    assert r.match("aggiungi un widget della mappa di Bologna").args["luogo"] == "bologna"
    assert r.match("metti un widget orologio di New York").args == {"tipo": "orologio", "luogo": "new york", "testo": ""}
    assert r.match("togli il widget del meteo").tool == "remove_widget"
    assert r.match("che tempo fa?") is None and r.match("metti la musica") is None
    tools = {t.name: t for t in make_tools()}
    assert "Non so fare" in tools["add_widget"].func(tipo="gatto")
