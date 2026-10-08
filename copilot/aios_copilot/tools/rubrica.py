"""La rubrica a voce: «aggiungi Mario Rossi alla rubrica, 333 1234567», «che numero ha Giulia?», «la mail di Sara»."""

from __future__ import annotations

import re
from typing import Any, Callable

from ..fastpath import Intent, normalize
from ..rubrica import Rubrica, describe
from .base import Tool, params


def make_tools(rubrica: Callable[[], Rubrica] = Rubrica) -> list[Tool]:
    def find_contact(nome: str) -> str:
        found = rubrica().find(nome)
        if not found:
            return f"Non trovo «{nome}» in rubrica (telefono, posta e contatti di SoIA)."
        return "\n".join(describe(c) for c in found[:8])

    def add_contact(nome: str, telefono: str = "", email: str = "", compleanno: str = "") -> str:
        return rubrica().add(nome, telefono, email, compleanno)

    return [
        Tool("find_contact", "Cerca una persona in rubrica: numeri di telefono, email, compleanno (rubrica del telefono, "
             "della posta e di SoIA).", params(nome="Nome, numero o email", required=["nome"]), find_contact, reads_private=True),
        Tool("add_contact", "Aggiunge o aggiorna un contatto in rubrica.",
             params(nome="Nome e cognome", telefono="Numero (facoltativo)", email="Email (facoltativa)",
                    compleanno="Compleanno AAAA-MM-GG (facoltativo)", required=["nome"]), add_contact),
    ]


RE_ADD = re.compile(r"^(?:aggiungi|salva|metti)\s+(?P<n>[\w' .-]{2,50}?)\s+(?:alla|in|nella|nei)\s+(?:rubrica|contatti)"
                    r"(?:\s*[,:]?\s*(?:con\s+)?(?:(?:il\s+)?(?:numero|telefono|cell(?:ulare)?)\s*)?(?P<t>\+?[\d][\d .-]{5,18}\d))?"
                    r"(?:\s*[,:]?\s*(?:e\s+)?(?:con\s+)?(?:(?:la\s+)?(?:mail|email)\s*)?(?P<e>[^\s@]+@[^\s@]+\.\w+))?$")
RE_FIND = re.compile(r"^(?:che\s+numero\s+ha|qual\s*(?:e|è)\s+il\s+numero\s+di|(?:dammi|dimmi)\s+il\s+numero\s+di|numero\s+di"
                     r"|qual\s*(?:e|è)\s+la\s+mail\s+di|(?:dammi|dimmi)\s+la\s+mail\s+di|la\s+mail\s+di"
                     r"|quando\s+(?:e|è)\s+il\s+compleanno\s+di|cerca\s+in\s+rubrica)\s+(?P<n>[\w' .-]{2,50})$")


class ContactsRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        m = RE_ADD.match(low)
        if m:
            name = " ".join(w.capitalize() for w in m.group("n").split())
            return Intent("add_contact", {"nome": name, "telefono": (m.group("t") or "").strip(), "email": m.group("e") or ""})
        m = RE_FIND.match(low)
        if m:
            return Intent("find_contact", {"nome": m.group("n").strip()})
        return None
