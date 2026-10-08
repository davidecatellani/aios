"""L'AI in cloud a voce: «accendi l'AI in cloud», «usa Claude», «quanto ho speso di AI?»."""

from __future__ import annotations

import re
from typing import Any

from .. import cloud
from .base import Tool, params

def make_tools() -> list[Tool]:
    def cloud_status() -> str:
        return cloud.status_text()

    def set_cloud(acceso: str = "sì") -> str:
        on = str(acceso).lower() in ("sì", "si", "true", "1", "acceso", "accendi")
        if on and not cloud.key():
            return ("Per accendere l'AI in cloud serve una chiave di OpenRouter (openrouter.ai › Keys): incollala in "
                    "Impostazioni › AI in cloud. Resta nel portachiavi del PC.")
        conf = cloud.save_settings({"attivo": on})
        if not on:
            return "AI in cloud spenta: rispondo solo con i modelli sul PC."
        return (f"AI in cloud accesa con {conf['modello']}: la uso solo per le richieste difficili, entro "
                f"{conf['limite_giorno']:.2f} $ al giorno e {conf['limite_mese']:.2f} $ al mese.")

    def set_cloud_model(modello: str) -> str:
        if "/" in modello:
            chosen = {"id": modello.strip(), "gratis": modello.strip().endswith(":free")}
        else:
            try:
                chosen = cloud.pick(modello, cloud.models())  # il più recente di quella famiglia, da OpenRouter
            except cloud.CloudError as exc:
                return f"Non riesco a leggere i modelli di OpenRouter: {exc}."
        if not chosen:
            return (f"Non trovo «{modello}» tra i modelli di OpenRouter che usano gli strumenti. Prova con deepseek, "
                    "claude, gemini, gpt, mistral, qwen, anche «gratis».")
        cloud.save_settings({"modello": chosen["id"]})
        extra = (" È gratuito: ha un limite di richieste al giorno e il fornitore può usare le domande per "
                 "migliorare i suoi modelli." if chosen.get("gratis") else "")
        return f"Per l'AI in cloud uso {chosen['id']}.{extra}"

    def set_cloud_limits(giorno: float | None = None, mese: float | None = None) -> str:
        changes = {k: v for k, v in (("limite_giorno", giorno), ("limite_mese", mese)) if v is not None}
        conf = cloud.save_settings(changes)
        return f"Limiti dell'AI in cloud: {conf['limite_giorno']:.2f} $ al giorno, {conf['limite_mese']:.2f} $ al mese."

    return [
        Tool("cloud_status", "Dice se l'AI in cloud è accesa, con quale modello e quanto si è speso.", params(), cloud_status),
        Tool("set_cloud", "Accende o spegne l'AI in cloud (OpenRouter) per le richieste difficili.",
             params(acceso=("sì per accendere, no per spegnere", ["sì", "no"])), set_cloud),
        Tool("set_cloud_model", "Sceglie il modello dell'AI in cloud: deepseek, claude, gemini, gpt, mistral, qwen (anche «gratis») "
             "o un nome di OpenRouter.", params(modello="Il modello", required=["modello"]), set_cloud_model),
        Tool("set_cloud_limits", "Imposta i limiti di spesa dell'AI in cloud (dollari al giorno e al mese).",
             params(giorno="Dollari al giorno", mese="Dollari al mese"), set_cloud_limits),
    ]


RE_STATUS = re.compile(r"\b(?:quanto\s+ho\s+speso\s+(?:di|per\s+l')\s*(?:ai|intelligenza\s+artificiale|cloud)|"
                       r"(?:l')?ai\s+(?:in\s+)?cloud\s+(?:è|e)\s+accesa)\b")
