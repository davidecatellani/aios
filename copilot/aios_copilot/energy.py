"""Energia: decide Nova, imparando dalle abitudini dell'utente.

Niente soglie fisse. Nova osserva (solo in locale) quando l'utente mette in carica e
quanto consuma, separando giorni feriali e fine settimana, e a ogni momento si chiede:
«la batteria basta fino alla prossima ricarica prevista, con un margine?».

- Avanza molto → lavoro in sottofondo normale (più rado che in carica).
- Basta appena → il lavoro pesante (copia delle foto, ricerca Bluetooth) aspetta la
  ricarica; la sincronizzazione si dirada.
- Non basta → solo l'essenziale (chiamate, notifiche, ciò che l'utente chiede).
- Ricarica prevista a breve → anche con batteria abbondante si aspetta: costa meno.
- In carica (o senza batteria, come un PC fisso) → tutto.

Ogni decisione ha una spiegazione («perché non hai copiato le foto?»), e l'utente può
chiedere per qualche ora «risparmia batteria» o «massime prestazioni».
"""

from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .privacy import private_dir

SAMPLE_EVERY = 600  # secondi tra due osservazioni
KEEP_DAYS = 30
DEFAULT_DRAIN = 5.0  # %/ora finché Nova non ha imparato
DEFAULT_HOURS_TO_CHARGE = 10.0
# Le attività in sottofondo: intervallo normale in carica (secondi) e quanto «pesano».
ACTIVITIES = {
    "sincronizzazione": (120, "leggera"),
    "bluetooth": (300, "media"),
    "vicini": (20, "leggera"),  # cercare i propri dispositivi via Bluetooth quando non c'è una rete in comune
    "foto": (1800, "pesante"),
    "modelli": (3600, "pesante"),
}


@dataclass
class Reading:
    level: int | None  # % (None = nessuna batteria: PC fisso)
    charging: bool


def read_battery(root: Path = Path("/")) -> Reading:
    supplies = root / "sys/class/power_supply"
    level, charging, ac = None, False, False
    for dev in sorted(supplies.glob("*")) if supplies.is_dir() else []:
        kind = _read(dev / "type")
        if kind == "Battery" and _read(dev / "capacity").isdigit():
            level = int(_read(dev / "capacity"))
            charging = charging or _read(dev / "status") in ("Charging", "Full")
        elif kind in ("Mains", "USB", "USB_PD") and _read(dev / "online") == "1":
            ac = True
    return Reading(level, charging or (ac and level is not None))


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


@dataclass
class Decision:
    mode: str  # pieno | normale | risparmio | riserva
    intervals: dict[str, int | None]  # None = rimandato
    reason: str
    hours_to_charge: float = 0.0
    drain: float = 0.0
    details: dict[str, str] = field(default_factory=dict)


class EnergyBrain:
    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time,
                 battery: Callable[[], Reading] = read_battery):
        self.path = path or private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios") / "energia.json"
        self.clock, self.battery = clock, battery
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        self.samples: list[list[float]] = data.get("campioni", [])  # [t, livello, in carica]
        self.override: dict = data.get("scelta", {})

    def save(self) -> None:
        self.path.write_text(json.dumps({"campioni": self.samples, "scelta": self.override}))

    # --- osservare -----------------------------------------------------------------------------------
    def observe(self, reading: Reading | None = None) -> None:
        reading = reading or self.battery()
        now = self.clock()
        if reading.level is None:
            return
        if self.samples and now - self.samples[-1][0] < SAMPLE_EVERY and bool(self.samples[-1][2]) == reading.charging:
            return
        self.samples.append([now, reading.level, 1 if reading.charging else 0])
        cutoff = now - KEEP_DAYS * 86400
        self.samples = [s for s in self.samples if s[0] >= cutoff]
        self.save()

    # --- imparare ------------------------------------------------------------------------------------
    def drain_rate(self) -> float:
        """Consumo tipico a batteria (%/ora), dalla mediana dei tratti di scarica."""
        rates = []
        for a, b in zip(self.samples, self.samples[1:]):
            hours = (b[0] - a[0]) / 3600
            if not a[2] and not b[2] and 0.15 <= hours <= 6 and b[1] <= a[1]:
                rates.append((a[1] - b[1]) / hours)
        return round(statistics.median(rates), 2) if len(rates) >= 3 else DEFAULT_DRAIN

    def charge_starts(self) -> list[datetime]:
        return [datetime.fromtimestamp(b[0]) for a, b in zip(self.samples, self.samples[1:]) if not a[2] and b[2]]

    def hours_to_charge(self, now: datetime) -> tuple[float, bool]:
        """Ore alla prossima ricarica abituale (feriali e weekend a parte). → (ore, imparato?)"""
        weekend = now.weekday() >= 5
        starts = [s for s in self.charge_starts() if (s.weekday() >= 5) == weekend]
        days = {s.date() for s in starts}
        if len(days) < 3:
            return DEFAULT_HOURS_TO_CHARGE, False
        # ore del giorno in cui l'utente mette in carica in almeno un terzo dei giorni
        by_hour: dict[int, set] = {}
        for s in starts:
            by_hour.setdefault(s.hour, set()).add(s.date())
        habitual = sorted(h for h, d in by_hour.items() if len(d) >= max(2, len(days) / 3))
        if not habitual:
            return DEFAULT_HOURS_TO_CHARGE, False
        ahead = [(h - now.hour - now.minute / 60) % 24 for h in habitual]
        return round(min(ahead), 1), True

    # --- decidere ------------------------------------------------------------------------------------
    def decide(self, reading: Reading | None = None) -> Decision:
        reading = reading or self.battery()
        now_ts = self.clock()
        now = datetime.fromtimestamp(now_ts)
        normal = {k: v[0] for k, v in ACTIVITIES.items()}
        choice = self.override if self.override.get("fino", 0) > now_ts else {}
        if reading.level is None:
            return Decision("pieno", normal, "Questo computer non ha batteria: lavoro normalmente.")
        if reading.charging and choice.get("modo") != "risparmio":
            return Decision("pieno", normal, f"In carica ({reading.level}%): è il momento giusto per il lavoro pesante.")
        drain = self.drain_rate()
        hours, learned = self.hours_to_charge(now)
        need = drain * hours
        reserve = 10
        margin = reading.level - need - reserve
        habit = (f"di solito metti in carica tra circa {hours:.0f} ore" if learned else
                 "non conosco ancora le tue abitudini di ricarica, quindi sono prudente")
        base = f"Batteria al {reading.level}%, consumo circa {drain:.0f}% all'ora, {habit}"
        if choice.get("modo") == "prestazioni":
            iv = {k: v * 2 for k, v in normal.items()}
            return Decision("normale", iv, "Mi hai chiesto massime prestazioni: lavoro normalmente fino a "
                            f"{datetime.fromtimestamp(choice['fino']):%H:%M}.", hours, drain)
        if choice.get("modo") == "risparmio" or margin < 0:
            why = ("Mi hai chiesto di risparmiare" if choice.get("modo") == "risparmio" else
                   f"{base}: non basterebbe")
            return Decision("riserva", {"sincronizzazione": None, "bluetooth": None, "vicini": None, "foto": None,
                                        "modelli": None},
                            f"{why}. Tengo solo l'essenziale: chiamate, notifiche e ciò che mi chiedi tu.", hours, drain)
        if margin < 25 or (learned and hours <= 1.5):
            soon = learned and hours <= 1.5
            return Decision("risparmio", {"sincronizzazione": normal["sincronizzazione"] * 8, "bluetooth": None,
                                          "vicini": normal["vicini"] * 6, "foto": None, "modelli": None},
                            f"{base}. " + ("La ricarica è vicina: il lavoro pesante (foto, Bluetooth) lo faccio in carica, "
                                           "costa meno." if soon else
                                           "Basta, ma senza molto margine: copia delle foto e ricerca Bluetooth aspettano "
                                           "la ricarica; sincronizzo più di rado."), hours, drain)
        return Decision("normale", {"sincronizzazione": normal["sincronizzazione"] * 3, "bluetooth": normal["bluetooth"] * 4,
                                    "vicini": normal["vicini"] * 3, "foto": normal["foto"] * 2, "modelli": None},
                        f"{base}: c'è margine. Lavoro in sottofondo, ma più di rado che in carica; i modelli AI li "
                        "scarico solo in carica.", hours, drain)

    def choose(self, mode: str, hours: float = 3) -> str:
        """«risparmia batteria» / «massime prestazioni» / «decidi tu»."""
        if mode == "auto":
            self.override = {}
            self.save()
            return "Va bene: torno a decidere io in base alle tue abitudini."
        self.override = {"modo": mode, "fino": self.clock() + hours * 3600}
        self.save()
        until = datetime.fromtimestamp(self.override["fino"])
        return (f"Risparmio la batteria fino alle {until:%H:%M}: solo l'essenziale." if mode == "risparmio" else
                f"Massime prestazioni fino alle {until:%H:%M}: poi torno a decidere io.")

    def explain(self, activity: str = "") -> str:
        d = self.decide()
        if activity and activity in d.intervals:
            iv = d.intervals[activity]
            label = {"foto": "la copia delle foto", "bluetooth": "la ricerca dei dispositivi Bluetooth",
                     "sincronizzazione": "la sincronizzazione", "modelli": "lo scaricamento dei modelli AI",
                     "vicini": "la ricerca del telefono senza Wi-Fi"}[activity]
            state = (f"{label[0].upper() + label[1:]} è rimandata." if iv is None else
                     f"{label[0].upper() + label[1:]} la faccio ogni {iv // 60} minuti circa.")
            return f"{state} {d.reason}"
        return d.reason


def due(last: float, interval: int | None, now: float) -> bool:
    return interval is not None and now - last >= interval

