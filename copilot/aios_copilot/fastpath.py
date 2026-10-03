"""Motore di intenti: esegue i comandi comuni senza interpellare il modello linguistico.

La maggior parte delle richieste a un sistema operativo è prevedibile ("apri
Firefox", "installa VLC", "apri i Download"). Riconoscerle con regole e con la
conoscenza che il sistema ha già di sé (app installate, cartelle, catalogo) costa
microsecondi su qualsiasi CPU, mentre anche un modello piccolo senza GPU impiega
secondi. Il modello entra in gioco solo quando questo livello non è sicuro.

Regola d'oro: in caso di dubbio non si indovina, si restituisce None e decide l'LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .tools import apps

# Riempitivi di cortesia che non cambiano il significato del comando.
_FILLER_START = re.compile(
    r"^(?:(?:ehi|hey|ok|allora|copilota)[,\s]+)*"
    r"(?:(?:per favore|perfavore|please)\s+)?"
    r"(?:(?:puoi|potresti|riesci a|vorrei|voglio|can you|could you|please)\s+)?"
    r"(?:mi\s+)?"
)
_FILLER_END = re.compile(r"(?:\s+(?:per favore|perfavore|please|grazie|thanks))*\s*[.!?]*$")
_ARTICLES = re.compile(
    r"^(?:(?:il|lo|la|i|gli|le|un|uno|una|the|a|an|my|mio|mia|miei|mie|"
    r"programma|applicazione|app|cartella|folder|sito|di|dei|delle|degli)\s+|(?:l'|un')\s*)+"
)

OPEN = r"(?:apri|aprire|aprimi|avvia|avviare|lancia|lanciare|mostra|mostrami|vai su|vai a|open|launch|start|run|show)"
INSTALL = r"(?:installa|installare|installami|scarica e installa|install)"
SEARCH = r"(?:cerca|cercami|cercare|ricerca|search|google|look up)"
WEB = r"(?:\s+(?:su internet|online|sul web|in rete|on the web|on internet))?"

DOMAIN = re.compile(r"^(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?$")

# Cartelle utente secondo la specifica XDG (nomi italiani e inglesi).
FOLDERS = {
    "download": "DOWNLOAD", "downloads": "DOWNLOAD", "scaricati": "DOWNLOAD",
    "documenti": "DOCUMENTS", "documents": "DOCUMENTS",
    "immagini": "PICTURES", "foto": "PICTURES", "pictures": "PICTURES", "photos": "PICTURES",
    "musica": "MUSIC", "music": "MUSIC",
    "video": "VIDEOS", "videos": "VIDEOS",
    "scrivania": "DESKTOP", "desktop": "DESKTOP",
    "home": "HOME", "cartella personale": "HOME",
}
XDG_DEFAULTS = {
    "DOWNLOAD": "Downloads", "DOCUMENTS": "Documents", "PICTURES": "Pictures",
    "MUSIC": "Music", "VIDEOS": "Videos", "DESKTOP": "Desktop", "HOME": "",
}

APP_SEARCH = re.compile(
    r"^(?:un |una |dei |delle )?(?:programm[ai]|app|applicazion[ei]|software)\s+(?:per|che|to|for)\s+(?P<q>.+)$"
)
SYSTEM_INFO = re.compile(
    r"^(?:quant[ao]|how much)\s+(?:memoria|ram|spazio|disco|memory|disk|space)\b"
    r"|^(?:info(?:rmazioni)?|dettagli)\s+(?:(?:sul|del|di|about)\s+)?(?:sistema|computer|pc|dispositivo)$"
    r"|^system info$"
)


@dataclass
class Intent:
    tool: str
    args: dict[str, Any]


def resolve_folder(key: str, home: Path | None = None) -> Path:
    """Percorso reale di una cartella XDG, leggendo ~/.config/user-dirs.dirs se presente."""
    home = home or Path.home()
    if key == "HOME":
        return home
    try:
        for line in (home / ".config/user-dirs.dirs").read_text().splitlines():
            m = re.match(rf'^XDG_{key}_DIR="(.*)"$', line.strip())
            if m:
                return Path(m.group(1).replace("$HOME", str(home)))
    except OSError:
        pass
    return home / XDG_DEFAULTS[key]


def normalize(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip().lower())
    text = text.replace("’", "'")
    text = _FILLER_END.sub("", _FILLER_START.sub("", text))
    return text.strip()


def strip_articles(text: str) -> str:
    text = _ARTICLES.sub("", text)
    return re.sub(r"\s+(?:app|applicazione|programma)$", "", text).strip()


@dataclass
class FastPath:
    find_desktop: Callable[[str], Path | None] = apps.find_desktop_entry
    find_apps: Callable[[str], list[dict[str, str]]] | None = None
    home: Path | None = None
    rules: list[tuple[re.Pattern[str], Callable[[re.Match[str]], Intent | None]]] = field(init=False)

    def __post_init__(self) -> None:
        self.rules = [
            (re.compile(rf"^{OPEN}\s+(?P<x>.+)$"), self._open),
            (re.compile(rf"^{INSTALL}\s+(?P<x>.+)$"), self._install),
            (re.compile(rf"^{SEARCH}{WEB}\s+(?P<x>.+)$"), self._search),
            (SYSTEM_INFO, lambda m: Intent("system_info", {})),
        ]

    def match(self, text: str) -> Intent | None:
        clean = normalize(text)
        for pattern, handler in self.rules:
            m = pattern.match(clean)
            if m:
                return handler(m)
        return None

    def _open(self, m: re.Match[str]) -> Intent | None:
        target = strip_articles(m.group("x"))
        if target in FOLDERS:
            return Intent("open_location", {"target": str(resolve_folder(FOLDERS[target], self.home))})
        if DOMAIN.match(target):
            url = target if target.startswith(("http://", "https://")) else f"https://{target}"
            return Intent("open_location", {"target": url})
        if target and self.find_desktop(target) is not None:
            return Intent("launch_app", {"name": target})
        return None

    def _install(self, m: re.Match[str]) -> Intent | None:
        name = strip_articles(m.group("x"))
        if not name or self.find_apps is None:
            return None
        # Solo una corrispondenza esatta del nome è abbastanza sicura da non chiedere all'LLM.
        exact = [a for a in self.find_apps(name) if a["name"].lower() == name]
        flatpak = [a for a in exact if a["source"] == "flatpak"]
        chosen = (flatpak or exact or [None])[0]
        if chosen is None:
            return None
        return Intent("install_app", {"app_id": chosen["id"], "source": chosen["source"]})

    def _search(self, m: re.Match[str]) -> Intent | None:
        query = m.group("x").strip()
        app = APP_SEARCH.match(query)
        if app:
            return Intent("search_apps", {"query": app.group("q")})
        return Intent("search_web", {"query": query})
