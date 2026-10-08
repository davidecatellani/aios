"""«Annulla l'ultima cosa che hai fatto», «rimetti tutto com'era stamattina», «cosa hai cambiato oggi?» (azioni.py)."""

from __future__ import annotations

import re
from typing import Any

from .. import azioni
from ..fastpath import Intent, normalize
from .base import Tool, params

NUMBERS = {"due": 2, "tre": 3, "quattro": 4, "cinque": 5}


def make_tools() -> list[Tool]:
    def undo_last_action(quante: str = "1") -> str:
        n = NUMBERS.get(str(quante).strip().lower()) or (int(quante) if str(quante).strip().isdigit() else 1)
        return azioni.undo_last(n)

    def undo_actions_since(quando: str) -> str:
        start = azioni.since(quando)
        if start is None:
            return "Da quando? Dimmi per esempio «da stamattina», «nell'ultima ora» o «da ieri sera»."
        return azioni.undo_since(start)

    def list_actions() -> str:
        return azioni.describe()

    return [
        Tool("undo_last_action", "Annulla l'ultima cosa (o le ultime) che Nova ha cambiato: impegni, widget, impostazioni, "
             "temi, riordino dei file, personalizzazioni…", params([], quante="Quante azioni annullare (di solito 1)"),
             undo_last_action),
        Tool("undo_actions_since", "Rimette com'era tutto quello che Nova ha cambiato da un certo momento (es. «stamattina», "
             "«nell'ultima ora», «da ieri sera»).", params(["quando"], quando="Da quando"), undo_actions_since,
             requires_confirmation=True),
        Tool("list_actions", "Elenca le ultime cose che Nova ha cambiato (e che si possono annullare).", params(), list_actions),
    ]


RE_LAST = re.compile(r"^(?:annulla|disfa)\s+(?:l'ultima\s+(?:cosa|azione|modifica)|(?:quello|ciò|cio)\s+che\s+hai\s+(?:appena\s+)?fatto)"
                     r"(?:\s+che\s+hai\s+fatto)?$|^(?:annulla|disfa)\s+le\s+ultime\s+(?P<n>\d+|due|tre|quattro|cinque)\s+(?:cose|azioni|modifiche)"
                     r"(?:\s+che\s+hai\s+fatto)?$")
RE_SINCE = re.compile(r"^(?:rimetti|riporta)\s+(?:tutto\s+)?com'era\s+(?P<q>.+)$"
                      r"|^annulla\s+tutto(?:\s+(?:quello|ciò|cio)\s+che\s+hai\s+(?:fatto|cambiato))?\s+(?P<q2>(?:da|dalle|dalla|nell'|negli|nelle|di\s+|oggi|stamattina|ieri|stasera).*)$")
RE_LIST = re.compile(r"^(?:cosa|che\s+cosa)\s+hai\s+(?:fatto|cambiato|modificato)(?:\s+(?:oggi|di\s+recente|ultimamente|finora))?$"
                     r"|^(?:le\s+)?(?:tue\s+)?azioni(?:\s+di\s+nova)?$")


class ActionsRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        m = RE_LAST.match(low)
        if m:
            return Intent("undo_last_action", {"quante": m.group("n") or "1"})
        m = RE_SINCE.match(low)
        if m:
            return Intent("undo_actions_since", {"quando": (m.group("q") or m.group("q2")).strip()})
        if RE_LIST.match(low):
            return Intent("list_actions", {})
        return None
