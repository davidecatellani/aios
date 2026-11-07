"""Fuso orario a voce: «imposta il fuso orario di Londra», «metti l'ora italiana», «che fuso orario ho?»."""

from __future__ import annotations

import re
from typing import Any, Callable

from .. import fuso
from ..fastpath import Intent, normalize
from .base import Tool, params


def make_tools(on_change: Callable[[str], None] | None = None,
               apply: Callable[[str], bool] | None = None) -> list[Tool]:
    def set_timezone(luogo: str) -> str:
        zone = fuso.find(luogo)
        if not zone:
            return f"Non conosco il fuso orario di «{luogo}»: dimmi la città più vicina (es. Roma, Londra, New York)."
        ok = fuso.set_zone(zone, apply) if apply else fuso.set_zone(zone)
        if not ok:
            return f"Non sono riuscito a impostare il fuso {zone}."
        if on_change:
            on_change(zone)
        return f"Fuso orario impostato: {zone}."

    def get_timezone() -> str:
        zone = fuso.current()
        return f"Il fuso orario è {zone}." if zone and zone not in fuso.UNSET else "Il sistema è sull'ora UTC (nessun fuso impostato)."

    return [Tool("set_timezone", "Imposta il fuso orario del computer (per città, paese o nome come Europe/Rome).",
                 params(luogo="Città o paese del fuso orario"), set_timezone),
            Tool("get_timezone", "Dice il fuso orario impostato.", params(), get_timezone)]


RE_SET = re.compile(r"^(?:imposta|metti|cambia|usa)\s+(?:il\s+)?(?:fuso(?:\s+orario)?|l'ora|l'orario|ora|orario)\s+"
                    r"(?:di|del|della|dell'|a|in|su|sul|sulla)?\s*(?P<l>.+)$")
RE_GET = re.compile(r"^(?:che|quale)\s+fuso(?:\s+orario)?\s+(?:ho|c'e|è|e)(?:\s+impostato)?$")


class TimezoneRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        if RE_GET.match(low):
            return Intent("get_timezone", {})
        m = RE_SET.match(low)
        if m and fuso.find(m.group("l")):
            return Intent("set_timezone", {"luogo": m.group("l")})
        return None
