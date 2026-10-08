"""Raccolte automatiche e riordino: strumenti per il copilota e frasi riconosciute all'istante."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Callable

from .. import organize
from ..fastpath import Intent, normalize
from ..organize import MONTHS, Library
from ..xdg import FOLDERS_IT, resolve_folder
from .base import Tool, params

ALIASES = {"fatture": "Fatture e ricevute", "ricevute": "Fatture e ricevute", "scontrini": "Fatture e ricevute",
           "contratti": "Contratti", "tasse": "Banca e tasse", "banca": "Banca e tasse", "casa": "Casa",
           "salute": "Salute", "referti": "Salute", "auto": "Auto", "macchina": "Auto", "viaggi": "Viaggi",
           "biglietti": "Viaggi", "lavoro": "Lavoro", "scuola": "Scuola e studio", "studio": "Scuola e studio",
           "università": "Scuola e studio", "curriculum": "Curriculum", "manuali": "Manuali", "ricette": "Ricette",
           "foto": "Foto", "fotografie": "Foto", "screenshot": "Screenshot", "video": "Video", "giochi": "Giochi",
           "videogiochi": "Giochi", "app": "*app", "applicazioni": "*app", "programmi": "*app", "musica": "*musica",
           "canzoni": "*musica", "installer": "Installer", "documenti": "*documento"}


def make_tools(get_library: Callable[[], Library]) -> list[Tool]:
    pending: dict[str, list[tuple[str, str]]] = {}

    def list_collections() -> str:
        lib = get_library()
        rows = lib.collections()
        if not rows:
            return "Sto ancora catalogando i tuoi file: le raccolte saranno pronte dopo il primo riordino a riposo."
        music = sum(n for c, n in rows if c.startswith("Musica · "))
        lines = [f"  {c}: {n}" for c, n in rows if not c.startswith("Musica · ")]
        if music:
            lines.append(f"  Musica: {music} brani di {sum(1 for c, _ in rows if c.startswith('Musica · '))} artisti")
        return "Le tue raccolte (create da me, senza spostare nulla):\n" + "\n".join(lines) + \
               "\nLe trovi anche nella cartella Raccolte."

    def show_collection(what: str) -> str:
        lib = get_library()
        words = normalize(what).split()
        name = next((ALIASES[w] for w in words if w in ALIASES), None)
        if name is None:
            artist = next((c for c, _ in lib.collections() if c.startswith("Musica · ") and
                           c.removeprefix("Musica · ").lower() in what.lower()), None)
            name = artist or what.strip().capitalize()
        if name.startswith("*"):
            kind = name[1:]
            entries = lib.entries(kind=kind) + (lib.entries(kind="gioco") if kind == "app" else [])
        else:
            entries = lib.entries(collection=name)
        year = re.search(r"\b(20\d\d)\b", what)
        month = next((m for m in MONTHS if m in what.lower()), None)
        if year:
            entries = [e for e in entries if year.group(1) in (e.group or e.date)]
        if month:
            entries = [e for e in entries if month in e.group.lower() or
                       (e.date and MONTHS[int(e.date[5:7]) - 1] == month)]
        if not entries:
            return f"Non ho trovato niente per «{what}»."
        if entries[0].kind in ("app", "gioco"):
            by_group: dict[str, list[str]] = {}
            for e in entries:
                label = e.collection + (f" ({e.group})" if e.group else "")
                by_group.setdefault(label, []).append(e.detail.get("name", Path(e.path).stem))
            return "\n".join(f"{g}: {', '.join(sorted(n))}" for g, n in sorted(by_group.items()))
        groups = Counter(e.group for e in entries)
        head = f"{len(entries)} elementi" + (f" in {len(groups)} gruppi" if len(groups) > 1 else "")
        sample = "\n".join(f"  {Path(e.path).name}" + (f" — {e.group}" if e.group else "") for e in entries[:12])
        more = f"\n  … e altri {len(entries) - 12}" if len(entries) > 12 else ""
        return f"{name.lstrip('*').capitalize()}: {head}\n{sample}{more}"

    def tidy_plan(folder: str = "scrivania") -> str:
        lib = get_library()
        lib.refresh()
        key = FOLDERS_IT.get(folder.lower().strip())
        path = resolve_folder(key) if key else Path(folder).expanduser()
        if not path.is_dir():
            return f"Non trovo la cartella «{folder}»."
        moves = organize.plan_tidy(lib, path)
        if not moves:
            return f"{path.name} è già in ordine. ✨"
        pending["last"] = moves
        dests = Counter(str(Path(d).parent.relative_to(Path.home())) if Path(d).is_relative_to(Path.home())
                        else str(Path(d).parent) for _, d in moves)
        lines = [f"  {n} → {dest}" for dest, n in dests.most_common(8)]
        return (f"Riordino di {path.name}: sposterei {len(moves)} file in {len(dests)} cartelle:\n" + "\n".join(lines) +
                "\nDimmi «procedi con il riordino» per farlo; potrai sempre dire «annulla il riordino».")

    def tidy_apply() -> str:
        moves = pending.pop("last", None)
        if not moves:
            return "Prima chiedimi di riordinare una cartella, così ti mostro il piano."
        n = organize.apply_tidy(moves)
        get_library().refresh()
        return f"Fatto: {n} file sistemati. Se non ti piace, dimmi «annulla il riordino»."

    def tidy_undo() -> str:
        n = organize.undo_tidy()
        get_library().refresh()
        return f"Annullato: {n} file sono tornati dov'erano." if n else "Non c'è un riordino da annullare."

    def cleanup() -> str:
        suggestions = get_library().cleanup_suggestions()
        if not suggestions:
            return "Niente da ripulire. ✨"
        lines = [f"  {Path(p).name} — {why}" for p, why in suggestions[:15]]
        return (f"Potresti eliminare {len(suggestions)} file (non tocco niente senza che tu lo chieda):\n" + "\n".join(lines))

    return [
        Tool("list_collections", "Elenca le raccolte automatiche dei file dell'utente (documenti per argomento, foto, "
             "video, musica, app, giochi).", params(), list_collections, reads_private=True),
        Tool("show_collection", "Mostra una raccolta: es. «fatture 2025», «foto di agosto», «giochi», «musica di Lucio Dalla».",
             params(what="Cosa mostrare"), show_collection, reads_private=True),
        Tool("tidy_plan", "Prepara (senza eseguire) il riordino dei file sparsi in una cartella: scrivania, download...",
             params(folder="Cartella"), tidy_plan, reads_private=True),
        Tool("tidy_apply", "Esegue il riordino preparato con tidy_plan (annullabile).", params(), tidy_apply,
             requires_confirmation=True),
        Tool("tidy_undo", "Annulla l'ultimo riordino: i file tornano dov'erano.", params(), tidy_undo,
             requires_confirmation=True),
        Tool("cleanup_suggestions", "Suggerisce file da eliminare: duplicati, installer vecchi, scaricamenti interrotti.",
             params(), cleanup, reads_private=True),
    ]


WHAT = "|".join(sorted(ALIASES, key=len, reverse=True))
RE_LIST = re.compile(r"^(?:(?:le\s+)?mie\s+raccolte|come\s+sono\s+organizzati\s+i\s+miei\s+file|raccolte)$")
RE_SHOW = re.compile(
    rf"^(?:mostra(?:mi)?|fammi\s+vedere|apri|trova(?:mi)?|elenca)\s+(?:(?:le|i|gli|la|il|lo|l')\s*)?(?:mie\s+|miei\s+)?(?P<w>(?:{WHAT})\b.*)$"
    rf"|^(?:che|quali)\s+(?P<w2>giochi|app|applicazioni|programmi)\s+ho")
RE_TIDY = re.compile(r"^(?:riordina|metti\s+in\s+ordine|sistema|organizza)\s+(?:la\s+|le\s+|i\s+|il\s+|la\s+cartella\s+)?(?P<f>scrivania|desktop|download|scaricati|documenti|immagini|foto|video|musica)$")
RE_APPLY = re.compile(r"^(?:procedi(?:\s+con\s+il\s+riordino)?|sì,?\s+riordina|riordina pure|fai pure il riordino)$")
RE_UNDO = re.compile(r"^annulla\s+(?:il\s+)?riordino$")
RE_CLEAN = re.compile(r"^(?:cosa\s+posso\s+(?:eliminare|cancellare)|fai\s+pulizia|fare\s+pulizia|libera\s+spazio)")


class OrganizeRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text)
        if RE_LIST.match(low):
            return Intent("list_collections", {})
        m = RE_TIDY.match(low)
        if m:
            return Intent("tidy_plan", {"folder": m.group("f")})
        if RE_APPLY.match(low):
            return Intent("tidy_apply", {})
        if RE_UNDO.match(low):
            return Intent("tidy_undo", {})
        if RE_CLEAN.match(low):
            return Intent("cleanup_suggestions", {})
        m = RE_SHOW.match(low)
        if m:
            return Intent("show_collection", {"what": m.group("w") or m.group("w2")})
        return None
