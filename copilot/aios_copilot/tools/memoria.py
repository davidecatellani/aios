"""La memoria di Nova: cosa hai fatto e di cosa avete parlato (vedi diario.py).

«dove mi ero fermato?», «su cosa ho lavorato ieri?», «qual era quel sito sulle bici che ho visto
5 giorni fa?», «riprendiamo la conversazione di ieri», «spegni il diario».
"""

from __future__ import annotations

import re
from datetime import date

from .. import diario
from ..fastpath import Intent, normalize
from .base import Tool, params


def _day(giorno: str) -> date | None:
    return diario.parse_day(giorno or "oggi")


def make_tools() -> list[Tool]:
    def recall_day(giorno: str = "ieri") -> str:
        day = _day(giorno)
        if day is None:
            return f"Non capisco quale giorno sia «{giorno}». Prova con «ieri», «lunedì» o «5 giorni fa»."
        return diario.day_summary(day)

    def left_off() -> str:
        return diario.where_left_off()

    def search_memory(testo: str, giorno: str = "") -> str:
        day = _day(giorno) if giorno else None
        return diario.search(testo, day=day)

    def resume_conversation(giorno: str = "") -> str:
        day = _day(giorno) if giorno else None
        chats = diario.conversation(day) if day else diario.recent_context()
        if not chats:
            return "Non trovo conversazioni da riprendere."
        lines = ["Riprendiamo da qui:"] + [f"• {c['ora']} tu: «{c['domanda'][:120]}» — io: {c['risposta'][:160]}"
                                          for c in chats[-4:]]
        return "\n".join(lines) + "\nDimmi pure come andiamo avanti."

    def diary_switch(attivo: str | bool) -> str:
        attivo = attivo is True or str(attivo).lower() in ("si", "sì", "true", "acceso")
        diario.set_enabled(attivo)
        return "Il diario è acceso: ricorderò cosa fai, solo su questo computer." if attivo else \
            "Il diario è spento: da adesso non annoto più niente. Quello già scritto resta finché non lo cancelli."

    def forget_diary() -> str:
        n = diario.forget_all()
        return f"Ho cancellato il diario ({n} giorni)." if n else "Il diario era già vuoto."

    return [
        Tool("recall_day", "Racconta su cosa ha lavorato l'utente in un giorno passato: programmi, file, siti, "
             "domande a Nova.", params(giorno="Il giorno: oggi, ieri, lunedì, 5 giorni fa, 3/10"), recall_day),
        Tool("where_left_off", "Dice dove si era fermato l'utente: l'ultima cosa che stava facendo.", params(), left_off),
        Tool("search_memory", "Cerca nel passato dell'utente un sito visto, un documento aperto o una cosa detta a "
             "Nova.", params(["testo"], testo="Di cosa parlava (es. bici, contratto)", giorno="Quando, se lo dice (facoltativo)"),
             search_memory),
        Tool("resume_conversation", "Riprende una conversazione passata con Nova.",
             params([], giorno="Il giorno della conversazione (facoltativo)"), resume_conversation),
        Tool("diary_switch", "Accende o spegne il diario delle attività (la memoria di cosa fa l'utente).",
             params(attivo=("Acceso o spento", ["si", "no"])), diary_switch),
        Tool("forget_diary", "Cancella tutto il diario delle attività e delle conversazioni.", params(), forget_diary,
             requires_confirmation=True),
    ]


WHEN = r"(?P<g>oggi|ieri(?:\s+sera|\s+mattina)?|l'?altro\s*ieri|stamattina|\d+\s*(?:gg|giorni)\s+fa|\w+\s+giorni\s+fa" \
       r"|(?:una|la)\s+settimana\s+fa|(?:lo\s+|la\s+)?(?:scors[oa]\s+)?(?:lunedi|martedi|mercoledi|giovedi|venerdi|sabato|domenica)" \
       r"(?:\s+scors[oa])?|\d{1,2}/\d{1,2})"
RE_LEFT_OFF = re.compile(r"^(?:(?:e\s+)?(?:dove|a\s+che\s+punto)\s+(?:mi\s+)?(?:son|sono|ero|eravamo|siamo)\s*"
                         r"(?:fermat[oaie]|rimast[oaie])?|cosa\s+stavo\s+facendo|riprendiamo(?:\s+da\s+dove\s+eravamo"
                         r"(?:\s+rimasti)?)?)(?:\s+" + WHEN + r")?$")
RE_DAY = re.compile(r"^(?:mi\s+)?(?:ricordi|ricordami|dimmi|riassumi(?:mi)?|raccontami)?\s*(?:su\s+|a\s+)?"
                    r"(?:cosa|che\s+cosa|che)\s+(?:ho\s+(?:lavorato|fatto)|stavo\s+(?:lavorando|facendo)|abbiamo\s+fatto)"
                    r"\s+" + WHEN + r"$")
RE_SITE = re.compile(r"^(?:mi\s+ricordi\s+)?(?:qual|quale|che|com'?)\s*(?:era|e|è)?\s+(?:il\s+nome\s+di\s+)?"
                     r"(?:quel|quello|il|la|quella)?\s*(?P<k>sito|pagina|articolo|video|documento|file)\s+(?P<q>.*?)\s*"
                     r"(?:che\s+)?(?:ho|avevo)\s+(?:visto|guardato|letto|visitato|aperto)(?:\s+" + WHEN + r")?$")
RE_RESUME = re.compile(r"^(?:riprendi(?:amo)?|continuiamo)\s+(?:la\s+)?(?:conversazione|discorso|chiacchierata)"
                       r"(?:\s+(?:di\s+)?" + WHEN + r")?$")
RE_DIARY = re.compile(r"^(?P<a>accendi|attiva|spegni|disattiva)\s+(?:il\s+)?diario$")


class MemoryRouter:
    """Le frasi sulla memoria, senza modello AI."""

    def match(self, text: str) -> Intent | None:
        low = normalize(text).lower().strip(" .!?")
        low = re.sub(r"[àá]", "a", re.sub(r"[ìí]", "i", low))
        m = RE_DIARY.match(low)
        if m:
            return Intent("diary_switch", {"attivo": "si" if m.group("a") in ("accendi", "attiva") else "no"})
        m = RE_LEFT_OFF.match(low)
        if m:
            return Intent("recall_day", {"giorno": m.group("g")}) if m.group("g") and m.group("g") != "oggi" \
                else Intent("where_left_off", {})
        m = RE_DAY.match(low)
        if m:
            return Intent("recall_day", {"giorno": m.group("g")})
        m = RE_RESUME.match(low)
        if m:
            return Intent("resume_conversation", {"giorno": m.group("g") or ""})
        m = RE_SITE.match(low)
        if m:
            topic = re.sub(r"^(?:che\s+|su\s+|sul\s+|sulla\s+|sugli\s+|sulle\s+|sui\s+|di\s+|del\s+|della\s+|dove\s+)+",
                           "", m.group("q")).strip()
            return Intent("search_memory", {"testo": topic or m.group("k"), "giorno": m.group("g") or ""})
        return None
