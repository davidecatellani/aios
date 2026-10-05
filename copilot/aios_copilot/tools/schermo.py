"""Schermo AIOS a voce: «fammi vedere il PC da gaming», «manda la foto al portatile», «quali PC sono accesi?»."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..fastpath import Intent, normalize
from ..schermo import azioni
from .base import Tool, params

FOLDERS = ("DOWNLOAD", "DOCUMENTS", "DESKTOP", "PICTURES", "VIDEOS", "MUSIC")


def find_file(name: str) -> Path | None:
    """Un percorso, o il nome di un file nelle cartelle dell'utente."""
    p = Path(name).expanduser()
    if p.is_absolute() and p.exists():
        return p
    from ..xdg import resolve_folder

    low = name.lower().strip()
    for key in FOLDERS:
        folder = resolve_folder(key)
        if (folder / name).exists():
            return folder / name
        try:
            hit = next((f for f in folder.iterdir() if f.name.lower() == low or f.stem.lower() == low), None)
        except OSError:
            hit = None
        if hit is not None:
            return hit
    return None


def make_tools() -> list[Tool]:
    def show_remote_screen(chi: str = "") -> str:
        return azioni.open_viewer(chi)[1]

    def send_files_to_pc(chi: str, file: str) -> str:
        paths, missing = [], []
        for name in [f.strip() for f in re.split(r"[;\n]", file) if f.strip()]:
            found = find_file(name)
            (paths.append(found) if found else missing.append(name))
        if missing:
            return f"Non trovo {', '.join('«' + m + '»' for m in missing)}: dimmi il percorso o il nome esatto."
        return azioni.send(chi, paths)[1]

    def list_my_pcs() -> str:
        found = azioni.peers()
        if not found:
            return ("Non vedo altri tuoi PC accesi nella rete. Servono la stessa identità AIOS (stessa frase di "
                    "recupero) e la stessa rete.")
        return "Accesi adesso: " + ", ".join(p.nome for p in found) + ". Posso aprirne lo schermo o mandargli file."

    return [
        Tool("show_remote_screen", "Apre lo schermo di un altro PC dell'utente nella stessa rete (desktop remoto di AIOS, "
             "con mouse, tastiera, suono e appunti condivisi).",
             params(chi="Nome del PC (es. «PC da gaming»); vuoto se ce n'è uno solo"), show_remote_screen),
        Tool("send_files_to_pc", "Manda file o cartelle a un altro PC dell'utente: arrivano nei suoi Scaricati.",
             params(chi="Nome del PC", file="Percorso o nome del file (più file separati da ;)", required=["chi", "file"]),
             send_files_to_pc),
        Tool("list_my_pcs", "Elenca gli altri PC dell'utente accesi nella rete.", params(), list_my_pcs),
    ]


PC = r"(?:pc|computer|portatile|fisso|desktop|mac)"
RE_SHOW = re.compile(r"^(?:fammi vedere|mostrami|apri|collegati (?:a|al|allo)|connettiti (?:a|al|allo)|entra (?:nel|nello)|"
                     r"vai (?:sul|sullo)|prendi il controllo (?:del|dello))\s+(?:(?:lo|il)\s+)?(?:schermo|desktop)?\s*"
                     r"(?:del|dello|dell'|di)?\s*(?P<chi>(?:il |l')?(?:altro )?" + PC + r"\b.*)$")
RE_LIST = re.compile(r"^(?:quali|che)\s+(?:altri\s+)?(?:pc|computer|dispositivi)\s+(?:sono accesi|vedi|ci sono)(?:\s+in rete)?$")


class ScreensRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        if RE_LIST.match(low):
            return Intent("list_my_pcs", {})
        m = RE_SHOW.match(low)
        if m:
            chi = re.sub(r"^(?:il |l')", "", m.group("chi")).strip()
            if chi in ("desktop", "computer", "pc", "mac", "schermo"):
                return None  # «apri il desktop»: non dice quale PC, ci pensa Nova
            return Intent("show_remote_screen", {"chi": chi})
        return None
