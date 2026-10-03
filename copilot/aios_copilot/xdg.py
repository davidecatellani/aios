"""Cartelle utente secondo la specifica XDG (es. ~/Scaricati su un sistema italiano)."""

from __future__ import annotations

import re
from pathlib import Path

XDG_DEFAULTS = {
    "DOWNLOAD": "Downloads", "DOCUMENTS": "Documents", "PICTURES": "Pictures",
    "MUSIC": "Music", "VIDEOS": "Videos", "DESKTOP": "Desktop", "HOME": "",
}


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


# Nomi con cui l'utente chiama le cartelle → chiave XDG.
FOLDERS_IT = {
    "scrivania": "DESKTOP", "desktop": "DESKTOP", "download": "DOWNLOAD", "scaricati": "DOWNLOAD",
    "documenti": "DOCUMENTS", "immagini": "PICTURES", "foto": "PICTURES", "video": "VIDEOS", "musica": "MUSIC",
}
