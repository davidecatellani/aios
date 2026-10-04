"""Cercare le foto dell'utente: «le foto di Aurora», «le foto di agosto», «foto del mare dell'anno scorso».

Si cerca nei nomi delle cartelle e dei file (album come «Compleanno Aurora», foto dal telefono) e nelle
date; le foto trovate compaiono come miniature accanto alla risposta. Riconoscere le persone dai volti
è un passo successivo (un modello locale che impara i volti indicati dall'utente).
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from .base import Tool, attach, params

IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".heic"}
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
          "novembre", "dicembre"]
SKIP_WORDS = set("""foto fotografie immagini immagine mostrami mostra fammi vedere trova cerca le la lo il i gli di del
della dei delle con in a al alla da su sul sulla che ho fatto fatte scattate mie miei mio mia tutte quelle quella
dove anno""".split())


def picture_roots(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    return [p for p in (home / "Immagini", home / "Pictures", home / "Scaricati", home / "Downloads",
                        home / "Telefono", home / "DCIM") if p.is_dir()]


def _plain(text: str) -> str:
    return (text.lower().replace("à", "a").replace("è", "e").replace("é", "e").replace("ì", "i")
            .replace("ò", "o").replace("ù", "u"))


def parse_query(query: str, today: date | None = None) -> tuple[list[str], tuple[int, int | None] | None]:
    """Parole da cercare e periodo (anno, mese) se la frase ne indica uno."""
    today = today or date.today()
    q = _plain(query)
    period: tuple[int, int | None] | None = None
    year = re.search(r"\b(19|20)\d{2}\b", q)
    if "anno scorso" in q:
        period = (today.year - 1, None)
    elif year:
        period = (int(year.group(0)), None)
    for i, m in enumerate(MONTHS, 1):
        if re.search(rf"\b{m}\b", q):
            y = period[0] if period else (today.year if i <= today.month else today.year - 1)
            period = (y, i)
    words = [w for w in re.findall(r"[a-z0-9]+", q)
             if len(w) > 2 and w not in SKIP_WORDS and w not in MONTHS and not re.fullmatch(r"(19|20)\d{2}", w)
             and w not in ("anno", "scorso", "scorsa")]
    return words, period


def find_photos(query: str, roots: list[Path] | None = None, today: date | None = None,
                limit: int = 24) -> list[Path]:
    words, period = parse_query(query, today)
    found: list[tuple[int, float, Path]] = []
    for root in roots if roots is not None else picture_roots():
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel = _plain(str(Path(dirpath).relative_to(root)))
            for f in files:
                p = Path(dirpath) / f
                if p.suffix.lower() not in IMAGES or f.startswith("."):
                    continue
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                if period:
                    when = datetime.fromtimestamp(mtime)
                    if when.year != period[0] or (period[1] and when.month != period[1]):
                        continue
                hay = rel + " " + _plain(f)
                score = sum(1 for w in words if w in hay)
                if words and not score:
                    continue
                found.append((score, mtime, p))
    found.sort(key=lambda t: (-t[0], -t[1]))
    return [p for _, _, p in found[:limit]]


def make_tools(roots: Callable[[], list[Path]] = picture_roots) -> list[Tool]:
    def show_photos(query: str) -> str:
        photos = find_photos(query, roots())
        if not photos:
            words, _ = parse_query(query)
            hint = (f" Per ora le riconosco dai nomi delle cartelle e dei file: se le foto di {words[0].capitalize()} "
                    f"sono in un album con un altro nome, dimmelo.") if words else ""
            return f"Non ho trovato foto per «{query}».{hint}"
        home = Path.home()
        items: list[dict[str, Any]] = []
        for p in photos:
            items.append({"titolo": p.name, "percorso": str(p), "sottotitolo":
                          datetime.fromtimestamp(p.stat().st_mtime).strftime("%d/%m/%Y"),
                          "cartella": str(p.parent).replace(str(home), "~")})
        attach("foto", items, f"Foto: {query}")
        folders = sorted({i["cartella"] for i in items})
        return (f"Ho trovato {len(photos)} foto" + (f" (in {', '.join(folders[:3])})" if folders else "") +
                ": sono qui accanto, toccane una per vederla grande.")

    return [Tool("show_photos", "Cerca e mostra le foto dell'utente per persona, luogo, evento o periodo "
                 "(es. «le foto di Aurora», «le foto di agosto», «il mare dell'anno scorso»).",
                 params(query="Cosa o chi cercare nelle foto, con il periodo se c'è"), show_photos, reads_private=True)]


RE_PHOTOS = re.compile(r"^(?:mostra(?:mi)?|fammi\s+vedere|trova(?:mi)?|cerca(?:mi)?|apri)\s+(?:le\s+|tutte\s+le\s+)?"
                       r"(?:mie\s+)?(?:foto|fotografie|immagini)\s+(?P<q>(?:di|del|della|dei|delle|con|in|a|al|alla|da|su|sul|sulla|che)\b.+)$")


class PhotosRouter:
    def match(self, text: str) -> Any:
        from ..fastpath import Intent, normalize

        m = RE_PHOTOS.match(normalize(text).strip(" .!?"))
        return Intent("show_photos", {"query": m.group("q")}) if m else None
