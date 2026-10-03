"""Cerca e legge i file dell'utente attraverso l'indice locale."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from .. import privacy
from ..fileindex import FileIndex
from .base import Tool, params


def make_tools(
    get_index: Callable[[], FileIndex],
    embed: Callable[[str], Sequence[float]] | None = None,
) -> list[Tool]:
    def search_files(query: str) -> str:
        index = get_index()
        results = index.search(query, embed=embed)
        if not results:
            stats = index.stats()
            if stats["files"] == 0:
                return ("L'indice dei file è ancora vuoto: lo costruisco quando il computer è a riposo "
                        "(oppure subito con: aios-learn --now).")
            return f"Nessun file trovato per '{query}'."
        return "\n".join(f"- {r['path']}\n  {r['snippet']}" for r in results)

    def read_file(path: str) -> str:
        text = get_index().read(Path(path))
        if text is None:
            return "Non posso leggere questo file: non è nell'indice oppure è in una cartella esclusa."
        return text

    def exclude_folder(path: str) -> str:
        folder = Path(path).expanduser()
        if not folder.is_dir():
            return f"La cartella {folder} non esiste."
        privacy.add_exclusion(folder)
        removed = get_index().forget_under(folder)
        return f"D'accordo: non leggerò più {folder.resolve()}. Ho tolto {removed} file dall'indice."

    return [
        Tool(
            "search_files",
            "Cerca tra i file e i documenti dell'utente (per parole e per significato). "
            "Restituisce percorsi e brevi estratti.",
            params(query="Cosa cercare, con parole chiave del contenuto o del nome del file"),
            search_files,
            reads_private=True,
        ),
        Tool(
            "read_file",
            "Legge il testo di un file dell'utente trovato con search_files.",
            params(path="Percorso completo del file"),
            read_file,
            reads_private=True,
        ),
        Tool(
            "exclude_folder",
            "Esclude una cartella: il copilota non la leggerà più e non la indicizzerà.",
            params(path="Percorso della cartella"),
            exclude_folder,
        ),
    ]
