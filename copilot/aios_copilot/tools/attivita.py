"""La gestione attività a voce: «cosa sta rallentando il PC?», «quanto è caldo il processore?», «chiudi a forza
Steam», «togli il modello dalla memoria»."""

from __future__ import annotations

import re
from typing import Any, Callable

from .. import attivita
from ..fastpath import Intent, normalize
from .base import Tool, params


def make_tools(manager: Callable[[], attivita.TaskManager] = attivita.shared) -> list[Tool]:
    def system_activity() -> str:
        return attivita.describe(manager().snapshot())

    def force_quit(programma: str) -> str:
        ok, msg = manager().end(programma)
        return msg

    def unload_ai_model(modello: str = "") -> str:
        models = attivita.loaded_models(manager().http) or []
        if not models:
            return "Nessun modello AI è caricato in memoria."
        chosen = [m for m in models if not modello or modello.lower() in m["nome"].lower()] or models
        done = [m["nome"] for m in chosen if attivita.unload_model(m["nome"], manager().http)]
        freed = sum(m["memoria_mb"] for m in chosen if m["nome"] in done)
        return (f"Ho tolto dalla memoria {', '.join(done)}: liberati {freed / 1024:.1f} GB. Si ricarica da solo alla prossima domanda."
                if done else "Non riesco a parlare con Ollama.")

    return [
        Tool("system_activity", "Lo stato del computer adesso: chi usa processore e memoria, temperature di processore e "
             "scheda video, ventole, modelli AI caricati e consigli (es. «cosa rallenta il PC?», «quanto è caldo?»).",
             params(), system_activity),
        Tool("force_quit", "Chiude a forza un programma bloccato o che consuma troppo (tutti i suoi processi).",
             params(programma="Il programma da chiudere", required=["programma"]), force_quit, requires_confirmation=True),
        Tool("unload_ai_model", "Toglie un modello AI dalla memoria (o dalla scheda video) per liberarla, es. prima di un gioco.",
             params(modello="Il modello (facoltativo: tutti)"), unload_ai_model),
    ]


RE_STATE = re.compile(r"^(?:cosa|chi)\s+(?:sta\s+)?(?:rallentando|rallenta|consuma|sta\s+consumando|usa|sta\s+usando)\b"
                      r"|^(?:perche|perché)\s+(?:il\s+(?:pc|computer)\s+)?(?:e|è|va)\s+(?:cosi\s+|così\s+)?(?:lento|lenta)"
                      r"|^(?:quanto\s+(?:e|è)\s+(?:caldo|calda)|che\s+temperatura|temperatura)\b"
                      r"|^(?:quanto|come)\s+(?:girano|va(?:nno)?)\s+le\s+ventole|^(?:velocita|velocità)\s+(?:delle\s+)?ventole"
                      r"|^(?:stato|carico)\s+del\s+(?:pc|computer|sistema)$|^come\s+sta\s+(?:il\s+)?(?:pc|computer)\b")
RE_FORCE = re.compile(r"^(?:chiudi\s+a\s+forza|forza\s+(?:la\s+)?chiusura\s+(?:di|del|della)|termina\s+a\s+forza|uccidi|killa)\s+"
                      r"(?:il\s+|la\s+|lo\s+|l')?(?P<n>[\w .'-]{2,40})$")
RE_UNLOAD = re.compile(r"^(?:togli|scarica|libera)\s+(?:il\s+|i\s+)?modell[oi](?:\s+(?:ai|di\s+ai))?\s+(?:dalla|dalle)\s+(?:memoria|ram|scheda\s+video|vram)$"
                       r"|^libera\s+la\s+(?:scheda\s+video|vram|memoria\s+della\s+scheda\s+video)$")


class ActivityRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        m = RE_FORCE.match(low)
        if m:
            return Intent("force_quit", {"programma": m.group("n").strip()})
        if RE_UNLOAD.match(low):
            return Intent("unload_ai_model", {})
        if RE_STATE.search(low):
            return Intent("system_activity", {})
        return None
