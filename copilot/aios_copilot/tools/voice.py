"""Nova a voce: accendere e spegnere l'ascolto continuo; ora e data (le prime domande che si fanno a voce)."""

from __future__ import annotations

import re
from datetime import datetime

from ..fastpath import Intent, normalize
from ..voice import listening_enabled, set_listening
from .base import Runner, Tool, params


def make_tools(runner: Runner | None = None) -> list[Tool]:
    runner = runner or Runner()

    def voice_listening(on: str = "sì") -> str:
        enabled = on.lower().strip() in ("sì", "si", "on", "accendi", "true", "yes")
        set_listening(enabled)
        if enabled:
            runner.run(["systemctl", "--user", "enable", "--now", "aios-voce.service"])
            return "Ti ascolto: di' «Nova» e parla. Tutto resta sul computer, niente viene registrato."
        return ("Non ascolto più: per parlarmi premi il microfono nella mia finestra, o di' «ascoltami» "
                "scrivendolo.")

    def voice_status() -> str:
        return ("Sono in ascolto della parola «Nova» (solo sul computer, niente viene registrato o inviato)."
                if listening_enabled() else "L'ascolto continuo è spento: dimmi «ascoltami» per riaccenderlo.")

    def current_time(what: str = "ora") -> str:
        now = datetime.now()
        days = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
        months = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
                  "ottobre", "novembre", "dicembre"]
        if what == "data":
            return f"Oggi è {days[now.weekday()]} {now.day} {months[now.month - 1]} {now.year}."
        if now.hour == 1:
            return f"È l'una e {now.minute:02d}." if now.minute else "È l'una."
        return f"Sono le {now.hour}:{now.minute:02d}."

    return [
        Tool("current_time", "Dice l'ora o la data di oggi.", params([], what=("Cosa", ["ora", "data"])), current_time),
        Tool("voice_listening", "Accende o spegne l'ascolto continuo della parola «Nova».",
             params(on=("Ascolto acceso?", ["sì", "no"])), voice_listening),
        Tool("voice_status", "Dice se Nova è in ascolto a voce.", params(), voice_status),
    ]


RE_OFF = re.compile(r"^(?:smetti\s+di\s+ascoltar(?:e|mi)|non\s+ascoltar(?:e|mi)(?:\s+più)?|spegni\s+(?:il\s+)?microfono"
                    r"|disattiva\s+(?:l'ascolto|la\s+voce))$")
RE_ON = re.compile(r"^(?:ascoltami(?:\s+sempre)?|torna\s+ad\s+ascoltare|accendi\s+(?:il\s+)?microfono"
                   r"|attiva\s+(?:l'ascolto|la\s+voce))$")
RE_STATUS = re.compile(r"^(?:mi\s+)?stai\s+ascoltando\??$|^sei\s+in\s+ascolto\??$")
RE_TIME = re.compile(r"^(?:che\s+)?or[ae]\s+(?:sono|è)|^(?:mi\s+dici\s+)?che\s+ore\s+sono|^(?:sai\s+)?che\s+ora\s+è")
RE_DATE = re.compile(r"^(?:che\s+)?(?:giorno|data)\s+(?:è|e)\s*(?:oggi)?$|^(?:quanti\s+ne\s+abbiamo|che\s+giorno\s+è\s+oggi)")


class VoiceRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text).rstrip("?!.")
        if RE_OFF.match(low):
            return Intent("voice_listening", {"on": "no"})
        if RE_ON.match(low):
            return Intent("voice_listening", {"on": "sì"})
        if RE_STATUS.match(low):
            return Intent("voice_status", {})
        if RE_TIME.match(low):
            return Intent("current_time", {"what": "ora"})
        if RE_DATE.match(low):
            return Intent("current_time", {"what": "data"})
        return None
