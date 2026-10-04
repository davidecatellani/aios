"""I widget della home (colonna di destra): meteo, mappa, orologio, nota. Li aggiunge e li toglie Nova a voce
(«inserisci un widget per il meteo», «metti la mappa di Bologna», «togli il meteo») o l'utente con la ×.

Ogni tipo ha una funzione che prepara i dati da mostrare; per aggiungerne uno nuovo basta un'altra voce in
TIPI. Il meteo e i luoghi vengono da Open-Meteo (gratis, senza chiave), le mappe da OpenStreetMap, passando dal
computer (la pagina della home non esce su internet da sola). I widget stanno in ~/.config/aios/widget.json.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
import zoneinfo
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

UA = "AIOS/1.0 (https://github.com/davidecatellani/aios)"  # OpenStreetMap chiede di farsi riconoscere
GEO = "https://geocoding-api.open-meteo.com/v1/search"
METEO = "https://api.open-meteo.com/v1/forecast"
TILE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
METEO_EVERY = 900  # il meteo si aggiorna al massimo ogni 15 minuti

Fetch = Callable[[str], bytes]

# codici meteo WMO → parole e simbolo
CIELO = {0: ("sereno", "☀️"), 1: ("poco nuvoloso", "🌤️"), 2: ("parzialmente nuvoloso", "⛅"), 3: ("coperto", "☁️"),
         45: ("nebbia", "🌫️"), 48: ("nebbia", "🌫️"), 51: ("pioviggine", "🌦️"), 53: ("pioviggine", "🌦️"),
         55: ("pioviggine", "🌦️"), 61: ("pioggia debole", "🌧️"), 63: ("pioggia", "🌧️"), 65: ("pioggia forte", "🌧️"),
         71: ("neve debole", "🌨️"), 73: ("neve", "🌨️"), 75: ("neve forte", "❄️"), 80: ("rovesci", "🌦️"),
         81: ("rovesci", "🌧️"), 82: ("rovesci forti", "⛈️"), 95: ("temporale", "⛈️"), 96: ("temporale", "⛈️"),
         99: ("temporale con grandine", "⛈️")}
GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]


def http(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def store_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "widget.json"


def load() -> list[dict[str, Any]]:
    try:
        data = json.loads(store_path().read_text())
        return [w for w in data if isinstance(w, dict) and w.get("tipo") in TIPI]
    except (OSError, ValueError):
        return []


def save(widgets: list[dict[str, Any]]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(widgets, ensure_ascii=False, indent=1))
    tmp.replace(path)


def home_place() -> str:
    """Il luogo di casa, se l'utente non ne dice uno: la città del fuso orario (Europe/Rome → Roma)."""
    from . import fuso

    zone = fuso.current() or fuso.guess()
    city = zone.rsplit("/", 1)[-1].replace("_", " ") if "/" in zone else ""
    return {"Rome": "Roma", "Milan": "Milano", "London": "Londra", "Paris": "Parigi"}.get(city, city or "Roma")


def geocode(place: str, fetch: Fetch = http) -> dict[str, Any] | None:
    url = GEO + "?" + urllib.parse.urlencode({"name": place, "count": 1, "language": "it", "format": "json"})
    try:
        found = json.loads(fetch(url)).get("results") or []
    except (OSError, ValueError):
        return None
    if not found:
        return None
    r = found[0]
    return {"nome": r.get("name", place), "lat": r["latitude"], "lon": r["longitude"], "fuso": r.get("timezone", ""),
            "zona": ", ".join(x for x in (r.get("admin1"), r.get("country")) if x)}


def add(tipo: str, luogo: str = "", testo: str = "", fetch: Fetch = http) -> tuple[dict[str, Any] | None, str]:
    """→ (widget, messaggio). Un widget uguale già presente non si duplica."""
    if tipo not in TIPI:
        return None, f"Non so fare un widget «{tipo}». So fare: {', '.join(TIPI)}."
    widgets = load()
    w: dict[str, Any] = {"id": f"{tipo}-{int(time.time() * 1000) % 10**9}", "tipo": tipo}
    if tipo == "nota":
        if not testo.strip():
            return None, "Cosa scrivo nella nota?"
        w["testo"] = testo.strip()[:500]
    else:
        place = geocode(luogo or home_place(), fetch)
        if place is None:
            return None, f"Non trovo il luogo «{luogo}»." if luogo else "Non riesco a trovare il luogo (serve internet)."
        w["luogo"] = place
        same = next((x for x in widgets if x["tipo"] == tipo and x.get("luogo", {}).get("nome") == place["nome"]), None)
        if same:
            return same, f"C'è già: {TIPI[tipo]['nome']} di {place['nome']}."
    widgets.append(w)
    save(widgets)
    what = TIPI[tipo]["nome"] + (f" di {w['luogo']['nome']}" if "luogo" in w else "")
    return w, f"Fatto: ho aggiunto il widget {what} a destra nella home."


def remove(match: str) -> str:
    """Toglie i widget per id, tipo o luogo («meteo», «la mappa di Bologna», «tutti i widget»)."""
    m = match.lower().strip()
    widgets = load()

    def hit(w: dict[str, Any]) -> bool:
        if w["id"] == match or "tutti" in m:
            return True
        place = w.get("luogo", {}).get("nome", "").lower()
        named = [t for t in TIPI if t in m]
        if named and w["tipo"] not in named:
            return False
        return (place and place in m) or (bool(named) and not any(x.get("luogo", {}).get("nome", "").lower() in m
                                                                   for x in widgets if x.get("luogo")))

    keep = [w for w in widgets if not hit(w)]
    if len(keep) == len(widgets):
        return "Non trovo quel widget nella home."
    save(keep)
    gone = len(widgets) - len(keep)
    return "Fatto: tolto dalla home." if gone == 1 else f"Fatto: tolti {gone} widget."


# --- i dati di ogni tipo -------------------------------------------------------------------------------------
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def meteo(w: dict[str, Any], fetch: Fetch = http, now: Callable[[], float] = time.time) -> dict[str, Any]:
    p = w["luogo"]
    key = f"{p['lat']:.3f},{p['lon']:.3f}"
    hit = _cache.get(key)
    if hit and now() - hit[0] < METEO_EVERY:
        return hit[1]
    url = METEO + "?" + urllib.parse.urlencode({
        "latitude": p["lat"], "longitude": p["lon"], "timezone": "auto", "forecast_days": 5,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"})
    d = json.loads(fetch(url))
    c = d["current"]
    sky, icon = CIELO.get(int(c["weather_code"]), ("", "🌡️"))
    days = []
    for i, day in enumerate(d["daily"]["time"]):
        s, ic = CIELO.get(int(d["daily"]["weather_code"][i]), ("", "🌡️"))
        days.append({"giorno": "oggi" if i == 0 else GIORNI[datetime.fromisoformat(day).weekday()], "simbolo": ic, "cielo": s,
                     "max": round(d["daily"]["temperature_2m_max"][i]), "min": round(d["daily"]["temperature_2m_min"][i]),
                     "pioggia": d["daily"].get("precipitation_probability_max", [None] * 7)[i]})
    out = {"titolo": f"Meteo · {p['nome']}", "simbolo": icon, "temperatura": round(c["temperature_2m"]), "cielo": sky,
           "percepita": round(c["apparent_temperature"]), "vento": round(c["wind_speed_10m"]),
           "umidita": c.get("relative_humidity_2m"), "giorni": days}
    _cache[key] = (now(), out)
    return out


def tile_of(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def mappa(w: dict[str, Any], fetch: Fetch = http, now: Callable[[], float] = time.time) -> dict[str, Any]:
    p = w["luogo"]
    z = int(w.get("zoom", 13))
    x, y = tile_of(p["lat"], p["lon"], z)
    return {"titolo": f"Mappa · {p['nome']}", "zoom": z, "x": x, "y": y, "zona": p.get("zona", ""),
            "link": f"https://www.openstreetmap.org/?mlat={p['lat']}&mlon={p['lon']}#map={z}/{p['lat']}/{p['lon']}"}


def orologio(w: dict[str, Any], fetch: Fetch = http, now: Callable[[], float] = time.time) -> dict[str, Any]:
    p = w["luogo"]
    zone = p.get("fuso") or "UTC"
    try:
        t = datetime.fromtimestamp(now(), zoneinfo.ZoneInfo(zone))
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        t = datetime.fromtimestamp(now())
    return {"titolo": f"Ora · {p['nome']}", "fuso": zone, "ora": t.strftime("%H:%M"),
            "data": f"{GIORNI[t.weekday()]} {t.day}", "scarto": t.utcoffset().total_seconds() / 3600 if t.utcoffset() else 0}


def nota(w: dict[str, Any], fetch: Fetch = http, now: Callable[[], float] = time.time) -> dict[str, Any]:
    return {"titolo": "Nota", "testo": w.get("testo", "")}


TIPI: dict[str, dict[str, Any]] = {
    "meteo": {"nome": "meteo", "dati": meteo},
    "mappa": {"nome": "mappa", "dati": mappa},
    "orologio": {"nome": "orologio", "dati": orologio},
    "nota": {"nome": "nota", "dati": nota},
}


def render(fetch: Fetch = http) -> list[dict[str, Any]]:
    """I widget con i dati da mostrare (se un servizio non risponde, il widget lo dice e resta)."""
    out = []
    for w in load():
        try:
            data = TIPI[w["tipo"]]["dati"](w, fetch)
        except Exception as exc:  # senza internet o servizio fermo: si mostra comunque
            data = {"titolo": f"{TIPI[w['tipo']]['nome'].capitalize()} · {w.get('luogo', {}).get('nome', '')}".strip(" ·"),
                    "errore": "non disponibile adesso" if isinstance(exc, OSError) else str(exc)[:80]}
        out.append({"id": w["id"], "tipo": w["tipo"], **data})
    return out


_tiles: dict[str, bytes] = {}


def tile(z: int, x: int, y: int, fetch: Fetch = http) -> bytes:
    """Un riquadro della mappa di OpenStreetMap, tenuto in memoria (la pagina non esce su internet da sola)."""
    if not (0 <= z <= 18 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise ValueError("riquadro fuori mappa")
    key = f"{z}/{x}/{y}"
    if key not in _tiles:
        if len(_tiles) > 400:
            _tiles.clear()
        _tiles[key] = fetch(TILE.format(z=z, x=x, y=y))
    return _tiles[key]
