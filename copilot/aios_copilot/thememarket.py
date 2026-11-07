"""Market dei temi: indice firmato, pacchetti controllati, pubblicazione dei propri temi.

Un pacchetto (.aiostheme, cioè uno zip) può contenere SOLO theme.json e un'immagine
di sfondo (PNG, JPEG o WebP). Niente codice, niente SVG, niente percorsi strani.
L'indice del market è firmato come il catalogo dei modelli (stesse chiavi fidate) e
ogni pacchetto ha la sua impronta SHA-256. I temi ispirati a opere o marchi
(«personal_only») restano per uso personale e non si possono pubblicare.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import modelcatalog
from .themes import FONTS, STYLES, Theme, save, themes_dir

ALLOWED_IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_IMAGE = 8 * 2**20
MAX_PACKAGE = 12 * 2**20
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class ThemeError(ValueError):
    pass


@dataclass
class Listing:
    id: str
    name: str
    author: str
    description: str
    url: str
    sha256: str
    tags: list[str]
    license: str = "cc-by-4.0"


def validate(doc: dict) -> Theme:
    """Un tema arrivato da fuori: solo i campi previsti, colori e scelte valide."""
    try:
        theme = Theme.from_dict(doc)
    except (TypeError, KeyError) as exc:
        raise ThemeError(f"tema non valido: {exc}") from exc
    if not ID.match(theme.id):
        raise ThemeError("identificativo del tema non valido")
    for pal in (theme.light, theme.dark):
        for value in vars(pal).values():
            if not isinstance(value, str) or not HEX.match(value):
                raise ThemeError(f"colore non valido: {value!r}")
    if theme.font not in FONTS:
        theme.font = "Inter"
    theme.radius = max(0, min(28, int(theme.radius)))
    wp = theme.wallpaper or {}
    if wp.get("kind") == "ricetta":
        if wp.get("style") not in STYLES or not all(isinstance(c, str) and HEX.match(c) for c in wp.get("colors", [])):
            raise ThemeError("ricetta dello sfondo non valida")
        theme.wallpaper = {"kind": "ricetta", "style": wp["style"], "colors": wp["colors"][:6], "seed": int(wp.get("seed", 1))}
    elif wp.get("kind") == "immagine":
        name = str(wp.get("file", ""))
        if not re.fullmatch(r"wallpaper\.(png|jpe?g|webp)", name):
            raise ThemeError("nome dell'immagine di sfondo non valido")
        theme.wallpaper = {"kind": "immagine", "file": name}
    else:
        theme.wallpaper = {}
    theme.orb = [c for c in theme.orb if isinstance(c, str) and HEX.match(c)][:3]
    for text in (theme.name, theme.description, theme.author):
        if len(text) > 200:
            raise ThemeError("testi troppo lunghi")
    return theme


def install_package(data: bytes, expected_sha256: str | None = None, origin: str = "market") -> Theme:
    if len(data) > MAX_PACKAGE:
        raise ThemeError("pacchetto troppo grande")
    if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ThemeError("impronta del pacchetto non corrispondente")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ThemeError("pacchetto non leggibile") from exc
    names = z.namelist()
    for info in z.infolist():
        name = info.filename
        if name != "theme.json" and not re.fullmatch(r"wallpaper\.(png|jpe?g|webp)", name):
            raise ThemeError(f"file non ammesso nel pacchetto: {name}")  # niente codice, niente SVG, niente cartelle
        if info.file_size > MAX_IMAGE or info.is_dir():
            raise ThemeError(f"file non ammesso: {name}")
    if "theme.json" not in names:
        raise ThemeError("manca theme.json")
    theme = validate(json.loads(z.read("theme.json")))
    theme.origin = origin
    folder = themes_dir() / theme.id
    folder.mkdir(parents=True, exist_ok=True)
    if theme.wallpaper.get("kind") == "immagine":
        image = z.read(theme.wallpaper["file"])
        if not _looks_like_image(image):
            raise ThemeError("l'immagine di sfondo non è un'immagine valida")
        (folder / theme.wallpaper["file"]).write_bytes(image)
    save(theme)
    return theme


def _looks_like_image(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


def export_package(theme: Theme, for_market: bool = False) -> bytes:
    """Crea il pacchetto del tema (per un amico o per il market)."""
    if for_market and theme.personal_only:
        raise ThemeError("Questo tema è ispirato a un'opera o a un marchio: resta per uso personale e non si può "
                         "pubblicare sul market.")
    folder = themes_dir() / theme.id
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        doc = json.loads(theme.to_json())
        if theme.wallpaper.get("kind") == "immagine":
            image = folder / theme.wallpaper["file"]
            if image.suffix.lower() in ALLOWED_IMAGES and image.is_file():
                z.write(image, f"wallpaper{image.suffix.lower()}")
                doc["wallpaper"] = {"kind": "immagine", "file": f"wallpaper{image.suffix.lower()}"}
            else:
                doc["wallpaper"] = {}
        z.writestr("theme.json", json.dumps(doc, ensure_ascii=False, indent=1))
    return buffer.getvalue()


# --- indice del market ---------------------------------------------------------------------------------


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AIOS/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read(MAX_PACKAGE + 1)


def index_path() -> Path:
    return themes_dir().parent / "theme-market.json"


def update_index(url: str, fetch: Callable[[str], bytes] = _get, keys: list[bytes] | None = None) -> int:
    keys = modelcatalog.trusted_keys() if keys is None else keys
    if not keys:
        raise ThemeError("Nessuna chiave fidata configurata: per sicurezza non uso il market.")
    data = fetch(url)
    if not modelcatalog.verify(data, base64.b64decode(fetch(url + ".sig")), keys):
        raise ThemeError("firma dell'indice non valida")
    entries = json.loads(data).get("themes", [])
    clean = [e for e in entries if isinstance(e, dict) and ID.match(str(e.get("id", ""))) and str(e.get("url", "")).startswith("https://")
             and re.fullmatch(r"[0-9a-f]{64}", str(e.get("sha256", "")))]
    index_path().write_text(json.dumps(clean, ensure_ascii=False))
    return len(clean)


def listings() -> list[Listing]:
    try:
        return [Listing(e["id"], e.get("name", e["id"]), e.get("author", ""), e.get("description", ""), e["url"],
                        e["sha256"], list(e.get("tags", [])), e.get("license", "cc-by-4.0"))
                for e in json.loads(index_path().read_text())]
    except (OSError, ValueError, KeyError):
        return []


def search(query: str) -> list[Listing]:
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    hits = [(sum(w in f"{l.name} {l.description} {' '.join(l.tags)}".lower() for w in words), l) for l in listings()]
    return [l for score, l in sorted(hits, key=lambda x: -x[0]) if score or not words]


def install_listing(theme_id: str, fetch: Callable[[str], bytes] = _get) -> Theme:
    listing = next((l for l in listings() if l.id == theme_id), None)
    if listing is None:
        raise ThemeError(f"Il tema «{theme_id}» non è nel market.")
    return install_package(fetch(listing.url), listing.sha256)
