"""Il fuso orario: l'orologio deve mostrare l'ora di casa, non quella di Greenwich.

Se il sistema è rimasto in UTC (installazione senza fuso, o /etc/localtime mancante) SoIA lo
deduce dalla lingua del sistema (it_IT → Europe/Rome) e lo imposta con timedatectl: la regola polkit
di SoIA lo permette all'amministratore seduto al computer, senza finestre di password.
Si cambia anche a voce: «imposta il fuso orario di Londra», «metti l'ora italiana».
"""

from __future__ import annotations

import os
import subprocess
import time
import zoneinfo
from pathlib import Path
from typing import Callable

UNSET = {"", "UTC", "Etc/UTC", "Etc/UCT", "UCT", "GMT", "Etc/GMT", "Universal", "Zulu", "n/a"}
BY_COUNTRY = {"IT": "Europe/Rome", "SM": "Europe/San_Marino", "VA": "Europe/Vatican", "CH": "Europe/Zurich",
              "FR": "Europe/Paris", "DE": "Europe/Berlin", "AT": "Europe/Vienna", "ES": "Europe/Madrid",
              "PT": "Europe/Lisbon", "GB": "Europe/London", "IE": "Europe/Dublin", "NL": "Europe/Amsterdam",
              "BE": "Europe/Brussels", "PL": "Europe/Warsaw", "RO": "Europe/Bucharest", "GR": "Europe/Athens",
              "US": "America/New_York", "BR": "America/Sao_Paulo", "AR": "America/Argentina/Buenos_Aires"}
# nomi italiani delle città più chieste (gli altri si cercano tra i nomi inglesi dei fusi)
CITIES = {"roma": "Europe/Rome", "italia": "Europe/Rome", "italiana": "Europe/Rome", "londra": "Europe/London",
          "inghilterra": "Europe/London", "parigi": "Europe/Paris", "francia": "Europe/Paris",
          "berlino": "Europe/Berlin", "germania": "Europe/Berlin", "madrid": "Europe/Madrid",
          "spagna": "Europe/Madrid", "lisbona": "Europe/Lisbon", "atene": "Europe/Athens", "mosca": "Europe/Moscow",
          "new york": "America/New_York", "stati uniti": "America/New_York", "los angeles": "America/Los_Angeles",
          "tokyo": "Asia/Tokyo", "giappone": "Asia/Tokyo", "pechino": "Asia/Shanghai", "cina": "Asia/Shanghai",
          "svizzera": "Europe/Zurich", "zurigo": "Europe/Zurich", "vienna": "Europe/Vienna",
          "bruxelles": "Europe/Brussels", "varsavia": "Europe/Warsaw", "dublino": "Europe/Dublin",
          "san paolo": "America/Sao_Paulo", "buenos aires": "America/Argentina/Buenos_Aires",
          "sydney": "Australia/Sydney", "dubai": "Asia/Dubai", "il cairo": "Africa/Cairo", "cairo": "Africa/Cairo"}


def current(localtime: Path = Path("/etc/localtime")) -> str:
    """Il fuso del sistema («Europe/Rome»), o "" se non è impostato."""
    tz = os.environ.get("TZ", "").lstrip(":")
    if tz and "/" in tz and not tz.startswith("/"):
        return tz
    try:
        target = os.readlink(localtime)
    except OSError:
        return ""
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else ""


def guess(lang: str | None = None) -> str:
    """Il fuso più probabile dalla lingua del sistema (it_IT.UTF-8 → Europe/Rome)."""
    lang = lang if lang is not None else (os.environ.get("LC_TIME") or os.environ.get("LANG") or "")
    country = lang.split(".")[0].partition("_")[2].upper()
    return BY_COUNTRY.get(country, "Europe/Rome" if lang.startswith("it") or not lang else "")


def find(name: str) -> str:
    """Il fuso per una città, un paese o un nome IANA («Londra», «ora italiana», «Asia/Tokyo»)."""
    key = " ".join(name.lower().replace("_", " ").split())
    for word in ("ora ", "orario ", "fuso "):
        key = key.removeprefix(word)
    if key in CITIES:
        return CITIES[key]
    zones = zoneinfo.available_timezones()
    for z in sorted(zones):
        if z.lower() == key.replace(" ", "_") or z.rsplit("/", 1)[-1].lower().replace("_", " ") == key:
            return z
    return ""


def _set(zone: str) -> bool:
    try:
        return subprocess.run(["timedatectl", "set-timezone", zone], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure(apply: Callable[[str], bool] = _set, now: Callable[[], str] = current) -> str:
    """All'avvio: se il sistema è in UTC imposta il fuso dedotto dalla lingua. → il fuso da mostrare."""
    zone = now()
    if zone not in UNSET:
        return zone
    wanted = guess()
    if wanted and apply(wanted):
        time.tzset()
    return wanted or zone or "UTC"


def set_zone(zone: str, apply: Callable[[str], bool] = _set) -> bool:
    if zone not in zoneinfo.available_timezones() or not apply(zone):
        return False
    os.environ.pop("TZ", None)
    time.tzset()
    return True
