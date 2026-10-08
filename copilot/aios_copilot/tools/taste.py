"""Abbonamenti e consigli: strumenti per il copilota e frasi riconosciute all'istante."""

from __future__ import annotations

import re
from typing import Callable

from ..fastpath import Intent, normalize
from ..recommend import KINDS, Catalog, Profile, recommend
from ..subscriptions import Subscriptions, find_service
from .base import Tool, attach, params

KIND_WORDS = {"film": "film", "serie": "serie", "telefilm": "serie", "serie tv": "serie", "cartone": "cartone",
              "cartoni": "cartone", "cartone animato": "cartone", "animazione": "cartone", "app": "software",
              "programma": "software", "programmi": "software", "software": "software", "applicazione": "software",
              "gioco": "gioco", "giochi": "gioco", "videogioco": "gioco", "videogiochi": "gioco"}


WEB_LABEL = {"serie": "serie tv", "film": "film", "cartone": "cartoni animati", "software": "programmi per Linux",
             "gioco": "giochi per PC (Steam, anche su Linux)"}


def web_query(kind: str, request: str) -> str:
    """«che serie tv mi consigli? mi è piaciuta Supernatural» → «serie tv simili a Supernatural»."""
    label = WEB_LABEL.get(kind, kind)
    m = re.search(r"piaciut[oaie]\s+(?:molto\s+|tanto\s+)?(.+?)(?:[?.!,]|$)", request, re.I) or \
        re.search(r"\b(?:come|simil[ei] a)\s+(.+?)(?:[?.!,]|$)", request, re.I)
    if m:
        title = m.group(1).strip(" «»\"'")
        return f"{label} simili a {title} consigli"
    filler = {"che", "quale", "quali", "mi", "ci", "di", "da", "a", "ad", "un", "una", "uno", "il", "la", "lo", "le", "gli",
              "cosa", "ho", "hai", "voglia", "guardare", "vedere", "giocare", "provare", "nova", "per", "favore", "qualcosa",
              "serie", "tv", "film", "cartone", "cartoni", "animati", "gioco", "giochi", "videogioco", "videogiochi",
              "programma", "programmi", "app", "e", "o", "adesso", "stasera", "oggi"}
    extra = [w for w in re.findall(r"[\w']+", request.lower())
             if w not in filler and not re.match(r"(?:consigl|suggeri|propon)", w)]
    verb = {"gioco": "da giocare", "software": "da provare"}.get(kind, "da vedere")
    return f"{label} {verb} consigliati {' '.join(extra)}".strip()


def make_tools(get_subs: Callable[[], Subscriptions], get_catalog: Callable[[], Catalog],
               get_profile: Callable[[], Profile], web_search: Callable[[str], str] | None = None) -> list[Tool]:
    def from_web(kind: str, request: str) -> str:
        """Senza catalogo (o senza titoli adatti) i consigli vengono dal web: Nova li riassume con le fonti."""
        if web_search is None:
            return ("Il catalogo non è ancora disponibile su questo computer: cerca con search_web e proponi titoli "
                    "concreti.")
        found = web_search(web_query(kind, request))
        if found.startswith(("Errore", "Nessun")):
            return f"Il catalogo non è ancora disponibile e la ricerca web non ha dato risultati ({found[:80]})."
        return ("Il catalogo non è ancora disponibile: ecco cosa dice il web. Proponi all'utente 3-5 titoli concreti "
                f"presi da qui, con una riga sul perché e la fonte.\n\n{found}")

    def list_subscriptions() -> str:
        return get_subs().summary()

    def set_subscription(service: str, active: str = "sì") -> str:
        found = [find_service(part) for part in re.split(r",|\s+e\s+|\s+and\s+", service) if part.strip()]
        if not found or not all(found):
            return f"Non conosco il servizio «{service}»."
        yes = str(active).lower() in ("sì", "si", "yes", "true", "1", "attivo")
        for s in found:
            get_subs().declare(s, yes)
        names = ", ".join(s.name for s in found)
        return f"Segnato: {names} {'attivo ✓' if yes else 'non più attivo'}." + \
               (" Ne terrò conto nei consigli." if yes else "")

    def recommend_tool(kind: str, request: str = "") -> str:
        kind = KIND_WORDS.get(kind.lower().strip(), kind.lower().strip())
        if kind not in KINDS:
            return f"Posso consigliarti: {', '.join(KINDS)}. La musica arriva presto."
        catalog = get_catalog()
        if not catalog.items:
            return from_web(kind, request)  # va al modello (agent.DEAD_END), con i risultati del web davanti
        subs = get_subs()
        picks = recommend(catalog, get_profile(), subs, kind, request)
        if not picks:
            if kind in ("film", "serie", "cartone") and not subs.active_keys():
                return ("Non so ancora che abbonamenti hai: dimmelo («ho Netflix e Disney+»), oppure collega la posta. "
                        "Intanto posso proporti solo titoli gratuiti, e non ne ho trovati di adatti.")
            return from_web(kind, request)
        attach("media", [{"titolo": p.item.title, "anno": p.item.year, "tipo": p.item.kind, "sottotitolo": p.where,
                          "estratto": p.why, "immagine": p.item.poster} for p in picks], "Ti propongo")
        lines = [f"{n}. «{p.item.title}»{f' ({p.item.year})' if p.item.year else ''} — {p.where}\n   {p.why}"
                 for n, p in enumerate(picks, 1)]
        return "Ecco cosa ti propongo:\n" + "\n".join(lines)

    def rate(title: str, liked: str = "sì") -> str:
        yes = str(liked).lower() in ("sì", "si", "yes", "true", "1")
        item = next((i for i in get_catalog().items if i.title.lower() == title.lower().strip()), None)
        get_profile().rate(item.title if item else title.strip(), yes, item.genres if item else [])
        return (f"Bene, terrò conto che «{title}» ti è piaciuto." if yes
                else f"Capito, niente più titoli come «{title}».")

    return [
        Tool("list_subscriptions", "Elenca gli abbonamenti riconosciuti (dalle email, dalle app o dichiarati) e la spesa.",
             params(), list_subscriptions, reads_private=True),
        Tool("set_subscription", "Segna un abbonamento come attivo o non più attivo (es. Netflix, Spotify, Disney+).",
             params(service="Servizio", active=("sì se attivo, no se disdetto", ["sì", "no"])), set_subscription),
        Tool("recommend", "Consiglia film, serie, cartoni, software o giochi in base ai gusti e agli abbonamenti attivi.",
             params(["kind"], kind=("Cosa", list(KINDS)), request="Richiesta originale (es. «con i bambini»)"),
             recommend_tool, reads_private=True),
        Tool("rate", "Registra se un titolo è piaciuto o no, per migliorare i consigli.",
             params(title="Titolo", liked=("sì o no", ["sì", "no"])), rate),
    ]


_KINDS = "|".join(sorted(KIND_WORDS, key=len, reverse=True))
RE_SUBS = re.compile(r"^(?:(?:quali|che|quanti)\s+abbonamenti\s+ho|(?:i\s+)?miei\s+abbonamenti|"
                     r"quanto\s+spendo\s+(?:di|in|per(?:\s+gli)?)\s+abbonamenti|abbonamenti\s+attivi)")
RE_HAVE = re.compile(r"^(?:ho(?:\s+anche)?|sono\s+abbonat[oa]\s+a|mi\s+sono\s+abbonat[oa]\s+a|ho\s+attivato)\s+(?P<x>.+)$")
RE_HAVE_NOT = re.compile(r"^(?:non\s+ho\s+più|ho\s+disdetto|ho\s+cancellato|ho\s+disattivato|non\s+sono\s+più\s+abbonat[oa]\s+a)\s+(?P<x>.+)$")
RE_RECOMMEND = re.compile(
    rf"(?:consigli\w*|suggeris\w*|propon\w*)\s+(?:mi\s+|ci\s+)?(?:(?:un|una|qualche|dei|degli|delle)\s+|un'\s*)?(?P<kind>{_KINDS})\b"
    rf"|^(?:che|quale)\s+(?P<kind2>{_KINDS})\s+(?:guardo|guardiamo|vedo|vediamo|metto|mettiamo|scarico|installo)"
    r"|^cosa\s+(?:guardo|guardiamo|vedo|vediamo)\b"
    rf"|^(?:che|quale|quali)\s+(?P<kind3>{_KINDS})\s+(?:mi\s+|ci\s+)?(?:consigli\w*|suggeris\w*|propon\w*)"
)
RE_LIKED = re.compile(r"^(?:mi|ci)\s+(?:è|e)\s+piaciut[oa](?:\s+molto)?\s+(?P<t>.+)$"
                      r"|^(?P<t2>.+?)\s+(?:mi|ci)\s+(?:è|e)\s+piaciut[oa](?:\s+molto|\s+tanto)?$", re.I)
RE_DISLIKED = re.compile(r"^(?:non\s+(?:mi|ci)\s+(?:è|e)\s+piaciut[oa]|non\s+mi\s+interessa)\s+(?P<t>.+)$"
                         r"|^(?P<t2>.+?)\s+non\s+(?:mi|ci)\s+(?:è|e)\s+piaciut[oa]$", re.I)


class TasteRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text)
        if RE_SUBS.match(low):
            return Intent("list_subscriptions", {})
        for regex, active in ((RE_HAVE_NOT, "no"), (RE_HAVE, "sì")):
            m = regex.match(low)
            if m:
                services = [find_service(part) for part in re.split(r",|\s+e\s+|\s+and\s+", m.group("x"))]
                if services and all(services):  # «ho la febbre» non è un abbonamento
                    return Intent("set_subscription", {"service": ", ".join(s.name for s in services), "active": active})
        m = RE_RECOMMEND.search(low)
        if m:
            kind = KIND_WORDS.get(m.group("kind") or m.group("kind2") or m.group("kind3") or "", "film")
            if kind == "film" and re.search(r"bambin|figli|piccol", low):
                kind = "cartone"
            return Intent("recommend", {"kind": kind, "request": text})
        clean = re.sub(r"[!.]+$", "", text.strip())  # il titolo conserva le maiuscole
        for regex, liked in ((RE_DISLIKED, "no"), (RE_LIKED, "sì")):
            m = regex.match(clean)
            if m:
                title = (m.group("t") or m.group("t2")).strip(" «»\"'")
                return Intent("rate", {"title": title, "liked": liked})
        return None
