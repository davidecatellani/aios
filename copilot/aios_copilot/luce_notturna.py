"""Luce notturna: la sera lo schermo diventa più caldo (meno luce blu), la mattina torna normale.

- «dal tramonto all'alba» (calcolati per il luogo di casa: quello del widget meteo, o la città del fuso
  orario) oppure con orari scelti; o accesa adesso fino alla mattina dopo;
- il passaggio è graduale (mezz'ora), come su Windows, macOS e GNOME;
- con Hyprland la applica hyprsunset (che si regola senza riavviarsi: «hyprctl hyprsunset temperature»);
  in alternativa gammastep o wlsunset.

Le scelte stanno in ~/.config/aios/luce-notturna.json; il ciclo gira nella shell (shell/__init__.py).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

NEUTRAL = 6500
DEFAULTS = {"attiva": False, "modo": "sole", "inizio": "21:00", "fine": "07:00", "temperatura": 4000, "fino_a": ""}
RAMP = timedelta(minutes=30)
# coordinate di riserva per il fuso orario (senza widget meteo e senza internet)
ZONES = {"Europe/Rome": (41.9, 12.5), "Europe/London": (51.5, -0.1), "Europe/Paris": (48.9, 2.35),
         "Europe/Berlin": (52.5, 13.4), "Europe/Madrid": (40.4, -3.7), "Europe/Zurich": (47.4, 8.5),
         "Europe/Vienna": (48.2, 16.4), "Europe/Lisbon": (38.7, -9.1), "America/New_York": (40.7, -74.0),
         "America/Los_Angeles": (34.1, -118.2), "America/Sao_Paulo": (-23.5, -46.6), "Asia/Tokyo": (35.7, 139.7)}


def config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "luce-notturna.json"


def settings() -> dict[str, Any]:
    conf = dict(DEFAULTS)
    try:
        data = json.loads(config_path().read_text())
        conf.update({k: data[k] for k in DEFAULTS if k in data})
    except (OSError, ValueError):
        pass
    return conf


def save(changes: dict[str, Any]) -> dict[str, Any]:
    conf = settings()
    for k, v in changes.items():
        if k == "attiva":
            conf[k] = bool(v)
        elif k == "modo" and v in ("sole", "orari"):
            conf[k] = v
        elif k in ("inizio", "fine") and isinstance(v, str) and len(v) == 5 and v[2] == ":":
            datetime.strptime(v, "%H:%M")
            conf[k] = v
        elif k == "temperatura":
            conf[k] = int(max(2500, min(5500, int(v))))
        elif k == "fino_a":
            conf[k] = str(v)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(conf, indent=1))
    return conf


def home_coords() -> tuple[float, float]:
    try:
        from . import widget

        for w in widget.load():
            if w.get("luogo", {}).get("lat") is not None:
                return float(w["luogo"]["lat"]), float(w["luogo"]["lon"])
    except Exception:
        pass
    try:
        from . import fuso

        zone = fuso.current() or fuso.guess()
    except Exception:
        zone = ""
    return ZONES.get(zone, ZONES["Europe/Rome"])


def sun_times(day: date, lat: float, lon: float, utc_offset_h: float) -> tuple[datetime, datetime]:
    """Alba e tramonto (ora locale) con la formula della NOAA: un paio di minuti di errore, quanto basta."""
    n = day.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) - 0.006758 * math.cos(2 * gamma)
            + 0.000907 * math.sin(2 * gamma) - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    phi = math.radians(lat)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(decl))) - math.tan(phi) * math.tan(decl)
    cos_ha = max(-1.0, min(1.0, cos_ha))  # sole di mezzanotte o notte polare: si accontenta
    ha = math.degrees(math.acos(cos_ha))
    base = datetime.combine(day, datetime.min.time())
    rise = 720 - 4 * (lon + ha) - eqtime + utc_offset_h * 60
    sset = 720 - 4 * (lon - ha) - eqtime + utc_offset_h * 60
    return base + timedelta(minutes=rise), base + timedelta(minutes=sset)


def _offset(now: datetime) -> float:
    off = now.astimezone().utcoffset() if now.tzinfo is None else now.utcoffset()
    return off.total_seconds() / 3600 if off else 0.0


def window(now: datetime, conf: dict[str, Any], coords: Callable[[], tuple[float, float]] = home_coords) -> tuple[datetime, datetime]:
    """La notte che contiene «now», o la prossima: (inizio, fine)."""
    if conf["modo"] == "sole":
        lat, lon = coords()
        off = _offset(now)
        _, set_today = sun_times(now.date(), lat, lon, off)
        rise_today, _ = sun_times(now.date(), lat, lon, off)
        if now < rise_today:  # notte iniziata ieri
            return sun_times(now.date() - timedelta(days=1), lat, lon, off)[1], rise_today
        return set_today, sun_times(now.date() + timedelta(days=1), lat, lon, off)[0]
    h1, m1 = map(int, conf["inizio"].split(":"))
    h2, m2 = map(int, conf["fine"].split(":"))
    start = now.replace(hour=h1, minute=m1, second=0, microsecond=0)
    end = now.replace(hour=h2, minute=m2, second=0, microsecond=0)
    if end <= start:  # a cavallo della mezzanotte
        if now < end:
            start -= timedelta(days=1)
        else:
            end += timedelta(days=1)
    return start, end


def target(now: datetime, conf: dict[str, Any] | None = None, coords: Callable[[], tuple[float, float]] = home_coords) -> int:
    """La temperatura dello schermo adesso (6500 = normale)."""
    conf = conf or settings()
    warm = int(conf["temperatura"])
    if conf.get("fino_a"):
        try:
            if now < datetime.fromisoformat(conf["fino_a"]):
                return warm
        except ValueError:
            pass
    if not conf["attiva"]:
        return NEUTRAL
    start, end = window(now, conf, coords)
    if now < start - RAMP or now >= end:
        return NEUTRAL
    if now < start:  # sta arrivando la sera: scende piano
        k = (now - (start - RAMP)) / RAMP
    elif now >= end - RAMP:  # sta arrivando la mattina: risale
        k = (end - now) / RAMP
    else:
        k = 1.0
    return int(round(NEUTRAL - (NEUTRAL - warm) * max(0.0, min(1.0, k)), -1))


def status(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    conf = settings()
    start, end = window(now, conf)
    t = target(now, conf)
    return {**conf, "adesso": t, "accesa_ora": t < NEUTRAL, "da": start.strftime("%H:%M"), "a": end.strftime("%H:%M")}


def now_on(hours: float | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Accesa adesso: fino alla prossima mattina (o per qualche ora)."""
    now = now or datetime.now()
    until = now + timedelta(hours=hours) if hours else (now + timedelta(days=1 if now.hour >= 7 else 0)).replace(hour=7, minute=0)
    return save({"fino_a": until.isoformat(timespec="minutes")})


class Applier:
    """Porta lo schermo alla temperatura voluta, con lo strumento che c'è."""

    def __init__(self, run: Callable[[list[str]], int] | None = None, spawn: Callable[[list[str]], Any] | None = None,
                 which: Callable[[str], str | None] = shutil.which):
        self.run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, timeout=5).returncode)
        self.spawn = spawn or (lambda cmd: subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self.which = which
        self.proc: Any = None
        self.current = NEUTRAL

    def apply(self, temp: int) -> None:
        if temp == self.current:
            return
        if self.which("hyprsunset"):
            alive = self.proc is not None and self.proc.poll() is None
            if temp >= NEUTRAL:
                if alive and self.run(["hyprctl", "hyprsunset", "identity"]) != 0:
                    self._stop()
            elif not alive:
                self.proc = self.spawn(["hyprsunset", "-t", str(temp)])
            elif self.run(["hyprctl", "hyprsunset", "temperature", str(temp)]) != 0:
                self._stop()  # versione senza comandi: si riavvia con la temperatura nuova
                self.proc = self.spawn(["hyprsunset", "-t", str(temp)])
        else:
            self._stop()
            if temp < NEUTRAL:
                if self.which("gammastep"):
                    self.proc = self.spawn(["gammastep", "-P", "-O", str(temp)])
                elif self.which("wlsunset"):
                    self.proc = self.spawn(["wlsunset", "-T", str(temp + 1), "-t", str(temp)])
        self.current = temp

    def _stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except Exception:
                self.proc.kill()
        self.proc = None


def run(applier: Applier | None = None, every: int = 60, clock: Callable[[], datetime] = datetime.now) -> None:
    applier = applier or Applier()

    def mtime() -> float:
        try:
            return config_path().stat().st_mtime
        except OSError:
            return 0.0

    while True:
        seen = mtime()
        try:
            applier.apply(target(clock()))
        except Exception:
            pass
        for _ in range(every):  # una scelta appena cambiata nelle Impostazioni si vede subito
            time.sleep(1)
            if mtime() != seen:
                break
