"""Nova a voce: accendere e spegnere l'ascolto continuo."""

from __future__ import annotations

import re

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

    return [
        Tool("voice_listening", "Accende o spegne l'ascolto continuo della parola «Nova».",
             params(on=("Ascolto acceso?", ["sì", "no"])), voice_listening),
        Tool("voice_status", "Dice se Nova è in ascolto a voce.", params(), voice_status),
    ]


RE_OFF = re.compile(r"^(?:smetti\s+di\s+ascoltar(?:e|mi)|non\s+ascoltar(?:e|mi)(?:\s+più)?|spegni\s+(?:il\s+)?microfono"
                    r"|disattiva\s+(?:l'ascolto|la\s+voce))$")
RE_ON = re.compile(r"^(?:ascoltami(?:\s+sempre)?|torna\s+ad\s+ascoltare|accendi\s+(?:il\s+)?microfono"
                   r"|attiva\s+(?:l'ascolto|la\s+voce))$")
RE_STATUS = re.compile(r"^(?:mi\s+)?stai\s+ascoltando\??$|^sei\s+in\s+ascolto\??$")


class VoiceRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text).rstrip("?!.")
        if RE_OFF.match(low):
            return Intent("voice_listening", {"on": "no"})
        if RE_ON.match(low):
            return Intent("voice_listening", {"on": "sì"})
        if RE_STATUS.match(low):
            return Intent("voice_status", {})
        return None
