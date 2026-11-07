"""Le notifiche a voce: «cosa mi sono perso?», «non disturbarmi per un'ora», «togli il non disturbare»."""

from __future__ import annotations

import re
from typing import Any

from .. import notifiche as N
from ..fastpath import Intent, normalize
from .base import Tool, params


def make_tools(store: Any = None) -> list[Tool]:
    def get_store() -> N.Store:
        return store or N.Store()

    def notifications_summary(tutte: bool = False) -> str:
        text = N.summary(get_store().load(), only_unread=not tutte)
        get_store().mark_read()
        return text

    def do_not_disturb(stato: str = "accendi", minuti: int = 0) -> str:
        stato = (stato or "accendi").lower()
        if stato in ("spegni", "off", "no"):
            N.save({"attivo": False, "fino_a": ""})
            return "«Non disturbare» spento: le notifiche tornano a comparire."
        if minuti:
            conf = N.quiet_for(int(minuti))
            return f"«Non disturbare» fino alle {conf['fino_a'][11:16]}. Le notifiche le tengo da parte."
        if stato in ("stanotte", "domattina"):
            N.quiet_for(None)
            return "«Non disturbare» fino a domattina alle 7."
        N.save({"attivo": True})
        return "«Non disturbare» acceso: le notifiche non compaiono, le trovi nel centro notifiche (Super+N)."

    return [
        Tool("notifications_summary", "Le notifiche arrivate (non ancora lette, o tutte): da chi e cosa dicevano.",
             params(tutte="true per anche quelle già viste"), notifications_summary, reads_private=True),
        Tool("do_not_disturb", "«Non disturbare»: accende o spegne, anche per un certo numero di minuti o fino a domattina.",
             params(stato=("Cosa fare", ["accendi", "spegni", "domattina"]), minuti="Per quanti minuti (facoltativo)"),
             do_not_disturb),
    ]


NUM = {"un": 1, "una": 1, "mezz": 0.5, "due": 2, "tre": 3, "quattro": 4, "cinque": 5}
RE_MISSED = re.compile(r"^(?:cosa|che)\s+(?:mi\s+sono\s+perso|ho\s+perso|e\s+successo|è\s+successo)\b|^(?:leggimi|mostrami|dimmi)\s+(?:le\s+)?notifiche"
                       r"|^(?:ci\s+sono|ho)\s+(?:delle\s+)?notifiche|^notifiche\s+nuove")
RE_DND = re.compile(r"^(?P<v>attiva|accendi|metti|togli|spegni|disattiva)\s+(?:il\s+)?non\s+disturbare"
                    r"(?:\s+(?:per\s+(?P<n>\w+)\s*'?(?P<u>or[ae]|minut[oi])|fino\s+a\s+domattina|(?P<notte>stanotte)))?$"
                    r"|^non\s+disturbarmi(?:\s+per\s+(?P<n2>\w+)\s*'?(?P<u2>or[ae]|minut[oi]))?$")


class NotificationsRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        if RE_MISSED.search(low):
            return Intent("notifications_summary", {})
        m = RE_DND.match(low)
        if not m:
            return None
        if m.group("v") in ("togli", "spegni", "disattiva"):
            return Intent("do_not_disturb", {"stato": "spegni"})
        n, unit = m.group("n") or m.group("n2"), m.group("u") or m.group("u2")
        if n:
            value = float(n) if n.isdigit() else NUM.get(n.rstrip("a"), NUM.get(n, 1))
            return Intent("do_not_disturb", {"stato": "accendi", "minuti": int(value * (60 if unit.startswith("or") else 1))})
        if "domattina" in low or m.group("notte"):
            return Intent("do_not_disturb", {"stato": "domattina"})
        return Intent("do_not_disturb", {"stato": "accendi"})
