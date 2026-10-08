"""Batteria: Nova spiega come la gestisce e accetta una scelta temporanea."""

from __future__ import annotations

import re
from typing import Callable

from ..energy import EnergyBrain
from ..fastpath import Intent, normalize
from .base import Tool, params

ACTIVITY_WORDS = {"foto": "foto", "fotografie": "foto", "bluetooth": "bluetooth", "cuffie": "bluetooth",
                  "sincronizz": "sincronizzazione", "modelli": "modelli"}


def make_tools(brain: Callable[[], EnergyBrain] = EnergyBrain) -> list[Tool]:
    def energy_status(activity: str = "") -> str:
        return brain().explain(activity)

    def energy_choice(mode: str, hours: str = "3") -> str:
        try:
            h = max(0.5, min(24.0, float(hours)))
        except ValueError:
            h = 3.0
        return brain().choose(mode if mode in ("risparmio", "prestazioni", "auto") else "auto", h)

    return [
        Tool("energy_status", "Spiega come Nova sta gestendo la batteria (o perché un'attività è rimandata: foto, "
             "bluetooth, sincronizzazione, modelli).", params([], activity=("Attività", ["", "foto", "bluetooth",
                                                                                      "sincronizzazione", "modelli"])),
             energy_status),
        Tool("energy_choice", "Per qualche ora: risparmia batteria, massime prestazioni, oppure torna a far decidere Nova.",
             params(["mode"], mode=("Scelta", ["risparmio", "prestazioni", "auto"]), hours="Per quante ore"), energy_choice),
    ]


RE_STATUS = re.compile(r"^(?:come\s+(?:gestisci|va|sta(?:i)?)\s+(?:la\s+)?batteria|stato\s+della\s+batteria|"
                       r"quanto\s+dura\s+la\s+batteria)\??$")
RE_WHY = re.compile(r"^perch[eé]\s+non\s+(?:hai|stai)\s+(?P<what>.+?)\??$")
RE_SAVE = re.compile(r"^(?:risparmia|salva)\s+(?:la\s+)?batteria(?:\s+per\s+(?P<h>\d+)\s+ore)?$|^modalità\s+risparmio$")
RE_PERF = re.compile(r"^(?:massime\s+prestazioni|modalità\s+prestazioni|non\s+risparmiare\s+(?:la\s+)?batteria)"
                     r"(?:\s+per\s+(?P<h>\d+)\s+ore)?$")
RE_AUTO = re.compile(r"^(?:decidi\s+tu(?:\s+(?:la|per\s+la)\s+batteria)?|batteria\s+automatica|gestisci\s+tu\s+la\s+batteria)$")


class EnergyRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text).rstrip("?!.")
        if RE_STATUS.match(low):
            return Intent("energy_status", {})
        m = RE_WHY.match(low)
        if m:
            activity = next((v for k, v in ACTIVITY_WORDS.items() if k in m.group("what")), "")
            if activity:
                return Intent("energy_status", {"activity": activity})
        m = RE_SAVE.match(low)
        if m:
            return Intent("energy_choice", {"mode": "risparmio", "hours": m.group("h") or "3"})
        m = RE_PERF.match(low)
        if m:
            return Intent("energy_choice", {"mode": "prestazioni", "hours": m.group("h") or "3"})
        if RE_AUTO.match(low):
            return Intent("energy_choice", {"mode": "auto"})
        return None
