"""Documenti personali: mostrarli (anche sul telefono) e usarli per rispondere."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .. import documents as docs
from ..fastpath import Intent, normalize
from ..xdg import resolve_folder
from .base import Runner, Tool, attach, params

DOC_WORDS = r"(?:bollett\w*|fattur\w*|contratt\w*|document\w*|pdf|ricevut\w*|referto|referti|dieta|ricett\w*|scontrin\w*|" \
            r"preventiv\w*|estratto\s+conto|busta\s+paga|certificat\w*|lettera|modulo|curriculum|cv|biglietto|polizza)"


def make_tools(get_index: Callable[[], Any], runner: Runner | None = None,
               now: Callable[[], datetime] = datetime.now) -> list[Tool]:
    runner = runner or Runner()

    def open_on_pc(path: Path) -> str:
        cmd = ["xdg-open", str(path)] if runner.has("xdg-open") else ["gio", "open", str(path)]
        runner.spawn(cmd)
        return f"Apro «{path.name}»."

    def show_document(query: str) -> str:
        found = docs.find_documents(get_index(), query, now().date())
        if not found:
            return f"Non trovo un documento per «{query}» tra i file del PC."
        best = found[0][0]
        attach("file", [{"titolo": p.name, "percorso": str(p), "sottotitolo": str(p.parent).replace(str(Path.home()), "~")}
                        for p, _ in found[:6]], "Documenti")
        deliver = docs.deliver_to.get() or open_on_pc
        out = deliver(best)
        others = [p.name for p, _ in found[1:3]]
        return out + (f" (Altri simili: {', '.join(others)}.)" if others else "")

    def diet_today(when: str = "oggi") -> str:
        day, meal = docs.parse_when(when, now())
        found = docs.find_documents(get_index(), "dieta piano alimentare settimanale pranzo cena colazione", day)
        if not found:
            return "Non trovo la tua dieta tra i file del PC (es. un PDF con «dieta» nel nome o nel testo)."
        path = found[0][0]
        text = get_index().read(path, 60000) or ""
        section = docs.section_for_day(text, day, meal)
        label = docs.WEEKDAYS[day.weekday()] + (f" a {meal}" if meal in ("pranzo", "cena", "colazione") else
                                               " (spuntino)" if meal == "spuntino" else "")
        if section is None:
            return (f"Ho trovato la dieta («{path.name}») ma non riesco a capire quale parte è per {docs.WEEKDAYS[day.weekday()]}: "
                    "chiedimelo in modo più libero e la leggo con il modello AI.")
        return f"Secondo la tua dieta («{path.name}»), {label}:\n{section}"

    def shopping_list(topic: str = "dieta") -> str:
        found = docs.find_documents(get_index(), f"{topic} piano alimentare settimanale pranzo cena", now().date())
        if not found:
            return f"Non trovo «{topic}» tra i file del PC."
        path = found[0][0]
        groups = docs.categorize(docs.shopping_items(get_index().read(path, 60000) or ""))
        if not groups:
            return f"Non riesco a ricavare gli alimenti da «{path.name}»."
        text = docs.shopping_text(groups)
        out = resolve_folder("DOCUMENTS") / "Lista della spesa.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"# Lista della spesa ({now():%d/%m/%Y}, da {path.name})\n\n{text}\n")
        return (f"Lista della spesa per la settimana, dalla tua dieta («{path.name}»):\n{text}\n"
                f"L'ho salvata in {out}." + (" " + docs.deliver_to.get()(out) if docs.deliver_to.get() else
                                              " Dimmi «manda la lista della spesa al telefono» per averla con te."))

    return [
        Tool("show_document", "Trova e mostra un documento personale (es. «la bolletta della luce di luglio»): "
             "lo apre sul PC, o dal telefono lo rende apribile lì.", params(query="Cosa cercare"), show_document,
             reads_private=True),
        Tool("diet_today", "Cosa prevede la dieta dell'utente (PDF sul PC) per oggi, domani, un giorno o un pasto.",
             params([], when="Es. «oggi», «domani a cena», «martedì a pranzo»"), diet_today, reads_private=True),
        Tool("shopping_list", "Prepara la lista della spesa della settimana dalla dieta dell'utente e la salva.",
             params([], topic="Documento da usare (predefinito: dieta)"), shopping_list, reads_private=True),
    ]


RE_SHOW = re.compile(rf"^(?:fammi\s+vedere|mostrami|trovami|fammi\s+avere|apri(?:mi)?|dammi)\s+(?:la\s+|il\s+|lo\s+|l'|le\s+|i\s+|gli\s+|un[oa]?\s+)?"
                     rf"(?P<q>(?:ultim[aoie]\s+)?{DOC_WORDS}.*)$")
RE_DIET = re.compile(r"^(?:cosa|che\s+cosa)\s+(?:devo|posso|dovrei)\s+mangiare(?P<w>.*)$|^cosa\s+(?:c'è|prevede\s+la\s+dieta)\s+"
                     r"(?:per\s+)?(?:pranzo|cena|colazione)(?P<w2>.*)$|^cosa\s+prevede\s+la\s+(?:mia\s+)?dieta(?P<w3>.*)$")
RE_SHOPPING = re.compile(r"^(?:fammi|prepara(?:mi)?|crea(?:mi)?|scrivi(?:mi)?)\s+(?:la\s+)?lista\s+della\s+spesa(?P<t>.*)$")


class DocumentsRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text).rstrip("?!.")
        m = RE_DIET.match(low)
        if m:
            return Intent("diet_today", {"when": low})
        m = RE_SHOPPING.match(low)
        if m:
            return Intent("shopping_list", {"topic": "dieta"})
        m = RE_SHOW.match(low)
        if m:
            return Intent("show_document", {"query": m.group("q")})
        return None
