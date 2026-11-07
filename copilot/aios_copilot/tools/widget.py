"""Widget della home a voce: «inserisci un widget per il meteo», «aggiungi la mappa di Bologna», «togli il meteo»."""

from __future__ import annotations

import re
from typing import Any

from .. import widget
from ..fastpath import Intent, normalize
from .base import Tool, params


def make_tools() -> list[Tool]:
    def add_widget(tipo: str, luogo: str = "", testo: str = "") -> str:
        tipo = TIPO_ALIAS.get(tipo.lower().strip(), tipo.lower().strip())
        return widget.add(tipo, luogo, testo)[1]

    def remove_widget(quale: str) -> str:
        return widget.remove(quale)

    def list_widgets() -> str:
        ws = widget.load()
        if not ws:
            return "Nella home non ci sono widget. Posso aggiungere: " + ", ".join(widget.TIPI) + "."
        return "Widget nella home: " + "; ".join(
            w["tipo"] + (f" di {w['luogo']['nome']}" if w.get("luogo") else "") for w in ws) + "."

    return [
        Tool("add_widget", "Aggiunge un widget nella home (colonna di destra): meteo, mappa, orologio di una città, "
             "nota. Per meteo, mappa e orologio il luogo (vuoto = casa).",
             params(tipo=("Il tipo di widget", ["meteo", "mappa", "orologio", "nota"]), luogo="Città o luogo (facoltativo)",
                    testo="Testo della nota (solo per la nota)", required=["tipo"]), add_widget),
        Tool("remove_widget", "Toglie uno o più widget dalla home (per tipo o luogo, o «tutti»).",
             params(quale="Quale widget: meteo, mappa di Bologna, tutti…"), remove_widget),
        Tool("list_widgets", "Elenca i widget presenti nella home.", params(), list_widgets),
    ]


TIPO_ALIAS = {"tempo": "meteo", "previsioni": "meteo", "previsioni del tempo": "meteo", "cartina": "mappa",
              "ora": "orologio", "orario": "orologio", "appunto": "nota", "promemoria": "nota", "post-it": "nota"}
TIPI_RE = r"(?P<t>meteo|previsioni(?: del tempo)?|tempo|mappa|cartina|orologio|ora|orario|nota|appunto|post-it)"
RE_ADD = re.compile(r"^(?:inserisci|aggiungi|metti|mettimi|crea|fammi|voglio)\s+(?:un|il|la|una|l')?\s*(?:widget|riquadro)\s+"
                    r"(?:del(?:la|l')?|per\s+(?:il|la|l')?|di|con\s+(?:il|la|l')?|dell')?\s*" + TIPI_RE +
                    r"(?:\s+(?:di|a|per|del|della|su|in)\s+(?P<l>[^:]+?))?(?:\s*:\s*(?P<n>.+))?$")
RE_ADD2 = re.compile(r"^(?:inserisci|aggiungi|metti|mettimi)\s+(?:il|la|l'|un|una)?\s*" + TIPI_RE +
                     r"(?:\s+(?:di|a|per|del|della|su|in)\s+(?P<l>[^:]+?))?\s+(?:nella|in|sulla)\s+(?:home|schermata|pagina)"
                     r"(?:\s*:\s*(?P<n>.+))?$")
RE_REMOVE = re.compile(r"^(?:togli|rimuovi|elimina|cancella|leva)\s+(?:il|la|l'|i|gli|le)?\s*(?:widget\s+)?(?P<q>.*?)"
                       r"(?:\s+(?:dalla|dalla)\s+(?:home|schermata|pagina))?$")


class WidgetRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        m = RE_ADD.match(low) or RE_ADD2.match(low)
        if m:
            tipo = TIPO_ALIAS.get(m.group("t"), m.group("t"))
            return Intent("add_widget", {"tipo": tipo, "luogo": (m.group("l") or "").strip(), "testo": (m.group("n") or "").strip()})
        m = RE_REMOVE.match(low)
        if m and ("widget" in low or re.search(r"\b(?:meteo|mappa|orologio|nota)\b", m.group("q")) and
                  re.search(r"\b(?:home|widget)\b", low)):
            return Intent("remove_widget", {"quale": m.group("q") or "widget"})
        return None
