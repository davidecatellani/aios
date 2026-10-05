"""Lo schermo a voce: luce notturna («accendi la luce notturna», «schermo più caldo»), e i monitor
(risoluzione, frequenza, scala, rotazione, disposizione) — vedi monitor.py e luce_notturna.py."""

from __future__ import annotations

import re
from typing import Any

from .. import luce_notturna as LN
from ..fastpath import Intent, normalize
from .base import Tool, params


def make_tools() -> list[Tool]:
    def night_light(stato: str = "accendi", temperatura: int = 0, modo: str = "") -> str:
        stato = (stato or "accendi").lower()
        changes: dict[str, Any] = {}
        if temperatura:
            changes["temperatura"] = temperatura
        if modo in ("sole", "orari"):
            changes["modo"] = modo
        if stato in ("spegni", "off", "no"):
            LN.save({**changes, "attiva": False, "fino_a": ""})
            return "Luce notturna spenta: lo schermo torna normale."
        if stato in ("adesso", "ora", "subito"):
            LN.save(changes)
            conf = LN.now_on()
            return f"Luce notturna accesa adesso, fino alle {conf['fino_a'][11:16]}."
        if stato in ("piu calda", "più calda", "calda"):
            changes["temperatura"] = LN.settings()["temperatura"] - 500
        elif stato in ("meno calda", "piu fredda", "più fredda"):
            changes["temperatura"] = LN.settings()["temperatura"] + 500
        LN.save({**changes, "attiva": True})
        st = LN.status()
        when = "dal tramonto all'alba" if st["modo"] == "sole" else f"dalle {st['inizio']} alle {st['fine']}"
        now = " È già accesa." if st["accesa_ora"] else ""
        return f"Luce notturna attiva {when} (stasera dalle {st['da']}), a {st['temperatura']} K.{now}"

    return [
        Tool("night_light", "Luce notturna (meno luce blu la sera): accendi (in automatico dal tramonto all'alba o con orari), "
             "spegni, adesso (subito fino a domattina), più calda o meno calda.",
             params(stato=("Cosa fare", ["accendi", "spegni", "adesso", "più calda", "meno calda"]),
                    temperatura="Temperatura in kelvin, 2500-5500 (facoltativa)",
                    modo=("Quando (facoltativo)", ["sole", "orari"])), night_light),
    ]


RE_NIGHT = re.compile(r"^(?P<v>accendi|attiva|spegni|disattiva|togli|metti)\s+(?:la\s+)?(?:luce\s+notturna|modalita\s+notte|modalità\s+notte|filtro\s+(?:della\s+)?luce\s+blu)"
                      r"(?P<adesso>\s+(?:adesso|ora|subito))?$")
RE_WARM = re.compile(r"^(?:rendi\s+)?(?:lo\s+)?schermo\s+(?P<w>piu\s+caldo|più\s+caldo|meno\s+caldo|piu\s+freddo|più\s+freddo)$"
                     r"|^luce\s+notturna\s+(?P<w2>piu\s+calda|più\s+calda|meno\s+calda)$")


class DisplayRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        m = RE_NIGHT.match(low)
        if m:
            off = m.group("v") in ("spegni", "disattiva", "togli")
            return Intent("night_light", {"stato": "spegni" if off else ("adesso" if m.group("adesso") else "accendi")})
        m = RE_WARM.match(low)
        if m:
            w = (m.group("w") or m.group("w2")).replace("piu", "più")
            return Intent("night_light", {"stato": "più calda" if w.startswith("più cald") else "meno calda"})
        return None
