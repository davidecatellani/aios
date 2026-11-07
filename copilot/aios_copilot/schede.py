"""Le schede accanto alle risposte di Nova: film e serie con miniatura e collegamenti.

Quando l'utente chiede di film, serie, cartoni o libri e Nova risponde con dei titoli, la pagina mostra
una scheda per titolo: la miniatura e un riassunto da Wikipedia (gratuita, senza chiave), il collegamento
alla pagina e a JustWatch per sapere dove vederlo. Le immagini passano dal server locale (pochi siti
ammessi, salvate in ~/.cache/aios/miniature), così la pagina di SoIA non carica niente da fuori.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

MEDIA_QUESTION = re.compile(r"(?i)\b(?:serie|serie\s+tv|telefilm|film|cartoni|cartone|anime|documentari\w*|libri|libro|"
                            r"romanz\w+|fumett\w+|videogioc\w+|giochi)\b")
IMAGE_HOSTS = ("upload.wikimedia.org", "image.tmdb.org", "dl.flathub.org", "flathub.org")
USER_AGENT = "SoIA/1.0 (assistente locale)"
SUFFIX = {"serie": ["(serie televisiva)", "(serie animata)"], "film": ["(film)"], "libro": ["(romanzo)"]}
_cache: dict[str, dict[str, Any]] = {}


def cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "aios" / "miniature"


def kind_of(question: str) -> str:
    q = question.lower()
    if re.search(r"\bserie|telefilm|anime|cartoni", q):
        return "serie"
    if re.search(r"\blibr|romanz|fumett", q):
        return "libro"
    if re.search(r"\bgioc", q):
        return "gioco"
    return "film"


def extract_titles(answer: str, limit: int = 8) -> list[str]:
    """I titoli in una risposta: «Titolo», "Titolo", **Titolo**, o l'inizio delle righe di un elenco."""
    found: list[str] = []
    for m in re.finditer(r"«([^»\n]{2,60})»|\*\*([^*\n]{2,60})\*\*|\"([^\"\n]{2,60})\"", answer):
        found.append(next(g for g in m.groups() if g))
    if not found:
        for line in answer.splitlines():
            m = re.match(r"^\s*(?:\d+[.)]|[-•*])\s+(.{2,60}?)(?:\s*\((?:\d{4}|[^)]*)\)|\s+[-–—:]\s|:|$)", line)
            if m:
                found.append(m.group(1).strip(" *_"))
    out: list[str] = []
    for t in found:
        t = t.strip(" .,:;")
        if t and t.lower() not in (x.lower() for x in out) and len(t.split()) <= 8:
            out.append(t)
    return out[:limit]


def media_cards(question: str, answer: str) -> list[dict[str, Any]]:
    """Le schede da allegare a una risposta su film, serie, libri… (solo titolo e tipo: il resto lo chiede la pagina)."""
    if not MEDIA_QUESTION.search(question):
        return []
    kind = kind_of(question)
    return [{"titolo": t, "tipo": kind} for t in extract_titles(answer)]


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read())


def lookup(title: str, kind: str = "film", get: Callable[[str], Any] | None = None, lang: str = "it") -> dict[str, Any]:
    """Miniatura, riassunto e collegamenti per un titolo (Wikipedia). Senza rete: solo i collegamenti."""
    key = f"{kind}:{title.lower()}"
    if key in _cache:
        return _cache[key]
    get = get or _get_json
    card: dict[str, Any] = {"titolo": title, "tipo": kind,
                            "dove": "https://www.justwatch.com/it/cerca?q=" + urllib.parse.quote(title)
                            if kind in ("film", "serie") else ""}
    for candidate in [f"{title} {s}" for s in SUFFIX.get(kind, [])] + [title]:
        try:
            data = get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
                       + urllib.parse.quote(candidate.replace(" ", "_")))
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("type") == "disambiguation" or not data.get("extract"):
            continue
        card.update(estratto=data["extract"][:220],
                    url=((data.get("content_urls") or {}).get("desktop") or {}).get("page", ""),
                    immagine=(data.get("thumbnail") or {}).get("source", ""))
        break
    _cache[key] = card
    return card


def image_allowed(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return url.startswith("https://") and any(host == h or host.endswith("." + h) for h in IMAGE_HOSTS)


def thumbnail(url: str, fetch: Callable[[str], bytes] | None = None) -> tuple[bytes, str] | None:
    """La miniatura, scaricata una volta e tenuta in cache. → (dati, tipo) o None."""
    if not image_allowed(url):
        return None
    folder = cache_dir()
    name = hashlib.sha256(url.encode()).hexdigest()[:32]
    for ext, mime in ((".jpg", "image/jpeg"), (".png", "image/png"), (".webp", "image/webp"), (".svg", "image/svg+xml")):
        cached = folder / (name + ext)
        if cached.exists():
            return cached.read_bytes(), mime

    def _fetch(u: str) -> bytes:
        req = urllib.request.Request(u, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read(2_000_000)

    try:
        data = (fetch or _fetch)(url)
    except Exception:
        return None
    mime = ("image/png" if data[:4] == b"\x89PNG" else "image/webp" if data[8:12] == b"WEBP"
            else "image/svg+xml" if data.lstrip()[:4] in (b"<svg", b"<?xm") else "image/jpeg")
    if mime == "image/svg+xml":
        return None  # niente SVG da fuori: potrebbero contenere script
    ext = {"image/png": ".png", "image/webp": ".webp", "image/jpeg": ".jpg"}[mime]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name + ext)).write_bytes(data)
    return data, mime
