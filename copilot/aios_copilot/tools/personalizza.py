"""Nova che riprogramma AIOS: «voglio l'orologio rotondo», «metti la barra in basso», «togli l'ultima
personalizzazione», «rimetti AIOS originale», «condividi la personalizzazione dell'orologio»."""

from __future__ import annotations

import re
from typing import Any

from .. import codice, programmatore
from ..fastpath import Intent, normalize
from .base import Tool, params


def _restart_shell() -> None:
    """La shell riparte dal codice nuovo (dopo qualche secondo, il tempo di leggere la risposta)."""
    from .windows import _shell

    _shell("--riavvia")


def make_tools(restart: Any = _restart_shell) -> list[Tool]:
    def customize_system(richiesta: str, ritocca: str = "") -> str:
        r = programmatore.customize(richiesta, retouch=ritocca)
        if r.get("in_attesa"):
            return r["messaggio"]
        if not r["ok"]:
            return "Non ci sono riuscita: " + r["messaggio"]
        restart()
        return (f"{r['messaggio']} Tra qualche secondo la schermata si ricarica con la modifica. "
                "Se non ti piace dimmi «togli l'ultima personalizzazione».")

    def apply_pending_customization() -> str:
        r = programmatore.apply_pending()
        if r["ok"]:
            restart()
        return r["messaggio"]

    def undo_customization(quale: str = "ultima") -> str:
        ok, what = codice.undo(quale or "ultima")
        if not ok:
            return what
        restart()
        return f"Ho tolto «{what}»: la schermata si ricarica come prima."

    def list_customizations() -> str:
        items = codice.history()
        if not items:
            return "Non hai personalizzazioni: AIOS è quello originale. Chiedimi pure di cambiare qualcosa."
        return "Le tue personalizzazioni (dalla più recente):\n" + "\n".join(f"• {i['richiesta']}" for i in items)

    def reset_customizations() -> str:
        n = codice.reset_all()
        restart()
        return f"AIOS è tornato originale ({n} personalizzazioni tolte; restano recuperabili)."

    def share_customization(quale: str = "ultima") -> str:
        item = codice.find(quale)
        if item is None:
            return "Non trovo quella personalizzazione."
        path = codice.export(item["id"])
        return f"Ecco il file da dare a chi vuoi: {path}. Chi lo apre in AIOS vede cosa fa e decide se applicarlo."

    return [
        Tool("customize_system", "Modifica AIOS stesso (il codice del sistema) come chiede l'utente: aspetto o funzionamento di "
             "schermata, barra, orologio, widget, app, impostazioni (es. «voglio l'orologio rotondo», «la barra in basso», "
             "«nella gestione attività mostrami anche i dischi»). Ci vuole qualche minuto; si può annullare.",
             params(richiesta="La richiesta dell'utente, completa",
                    ritocca="Per cambiare una personalizzazione già fatta: quale (es. «orologio»); altrimenti vuoto",
                    required=["richiesta"]), customize_system),
        Tool("apply_pending_customization", "Applica la personalizzazione rimasta in attesa di conferma.", params(),
             apply_pending_customization, requires_confirmation=True),
        Tool("undo_customization", "Toglie una personalizzazione di AIOS (l'ultima o quella indicata).",
             params(quale="Quale: «ultima» o parole della richiesta (es. orologio)"), undo_customization),
        Tool("list_customizations", "Elenca le personalizzazioni fatte ad AIOS.", params(), list_customizations),
        Tool("reset_customizations", "Riporta AIOS allo stato originale togliendo tutte le personalizzazioni.", params(),
             reset_customizations, requires_confirmation=True),
        Tool("share_customization", "Prepara il file di una personalizzazione da dare a un altro utente.",
             params(quale="Quale personalizzazione"), share_customization),
    ]


RE_UNDO = re.compile(r"^(?:togli|annulla|elimina|rimuovi)\s+(?:l'|la\s+)?(?:ultima\s+)?personalizzazione"
                     r"(?:\s+(?:dell'|(?:del|della|dello|dei|delle|di)\s+)(?P<q>.+))?$")
RE_RESET = re.compile(r"^(?:rimetti|riporta|torna\s+a(?:d)?)\s+(?:aios\s+)?(?:originale|come\s+era\s+all'inizio|allo\s+stato\s+(?:iniziale|originale))"
                      r"|^(?:togli|annulla)\s+tutte\s+le\s+personalizzazioni$")
RE_LIST = re.compile(r"^(?:quali|che)\s+personalizzazioni\s+(?:ho|ci\s+sono)")
RE_APPLY = re.compile(r"^applica\s+(?:la\s+)?personalizzazione$")
RE_SHARE = re.compile(r"^(?:condividi|esporta)\s+(?:la\s+)?(?:mia\s+)?personalizzazione(?:\s+(?:dell'|(?:del|della|dello|di)\s+)(?P<q>.+))?$")
RE_DO = re.compile(r"^(?:riprogramma|modifica|cambia|personalizza)\s+aios\s*[:,]?\s*(?P<r>.+)$")


class CustomizeRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        if RE_RESET.match(low):
            return Intent("reset_customizations", {})
        m = RE_UNDO.match(low)
        if m:
            return Intent("undo_customization", {"quale": (m.group("q") or "ultima").strip()})
        if RE_LIST.match(low):
            return Intent("list_customizations", {})
        if RE_APPLY.match(low):
            return Intent("apply_pending_customization", {})
        m = RE_SHARE.match(low)
        if m:
            return Intent("share_customization", {"quale": (m.group("q") or "ultima").strip()})
        m = RE_DO.match(low)
        if m:
            return Intent("customize_system", {"richiesta": text.strip().split(None, 2)[-1].lstrip(":, ")})
        return None
