"""Consigli personalizzati: film, serie, cartoni, software e giochi.

Privacy per costruzione:
- da internet arrivano solo cataloghi GENERICI (popolari, novità, dove si guarda
  ciascun titolo), con richieste identiche per tutti gli utenti: non rivelano né
  i gusti né gli abbonamenti;
- il profilo dei gusti e la scelta finale stanno sul dispositivo.

Fonti dei cataloghi:
- film/serie/cartoni: TMDB (disponibilità per servizio fornita da JustWatch).
  Serve una chiave API gratuita (AIOS_TMDB_KEY o ~/.config/aios/catalogs.json);
- software e giochi: Flathub (API pubblica).
La musica dipende dalle API dei servizi (Spotify, Deezer...): prossimo passo.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .privacy import private_dir
from .subscriptions import SERVICES, Subscriptions

USER_AGENT = "SoIA/0.1"
REGION = "IT"
LANGUAGE = "it-IT"

# Generi TMDB (film e serie hanno identificativi in parte diversi).
GENRES = {
    16: "animazione", 10751: "famiglia", 35: "commedia", 18: "drammatico", 28: "azione", 12: "avventura",
    878: "fantascienza", 53: "thriller", 27: "horror", 10749: "romantico", 99: "documentario", 14: "fantasy",
    80: "crime", 9648: "mistero", 10762: "bambini", 10765: "fantascienza", 10759: "azione", 10764: "reality",
    36: "storico", 10402: "musica", 10752: "guerra", 37: "western",
}
KIDS_OK = {"animazione", "famiglia", "bambini", "avventura", "commedia", "fantasy"}
KIDS_NO = {"horror", "thriller", "crime", "guerra"}
KINDS = ("film", "serie", "cartone", "software", "gioco")


@dataclass
class Item:
    id: str  # "tmdb:movie:123", "flathub:org.gimp.GIMP"
    kind: str  # film | serie | software | gioco
    title: str
    genres: list[str] = field(default_factory=list)
    year: int = 0
    popularity: float = 0.0
    overview: str = ""
    providers: list[str] = field(default_factory=list)  # chiavi di SERVICES dove è incluso
    adult: bool = False
    new_season: str = ""  # data di uscita dell'ultima stagione (serie)
    poster: str = ""  # miniatura (locandina TMDB o icona Flathub)

    @property
    def animated(self) -> bool:
        return "animazione" in self.genres


def cache_dir() -> Path:
    return private_dir(Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "aios")


def data_dir() -> Path:
    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios")


def _get(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


# --- cataloghi -------------------------------------------------------------------------


def tmdb_key() -> str | None:
    key = os.environ.get("AIOS_TMDB_KEY")
    if key:
        return key
    try:
        cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "catalogs.json"
        return json.loads(cfg.read_text()).get("tmdb_key")
    except (OSError, ValueError, AttributeError):
        return None


def _provider_key(name: str) -> str | None:
    for s in SERVICES:
        if name in s.tmdb_names or name.lower() == s.name.lower():
            return s.key
    return None


class TMDBCatalog:
    """Popolari e novità di film e serie in Italia, con dove guardarli (inclusi in abbonamento o gratis)."""

    base = "https://api.themoviedb.org/3"

    def __init__(self, key: str, get: Callable[[str], Any] = _get, base: str | None = None):
        self.key, self.get = key, get
        self.base = base or self.base

    def _url(self, path: str, **params: Any) -> str:
        return f"{self.base}{path}?" + urllib.parse.urlencode({"api_key": self.key, "language": LANGUAGE, **params})

    def fetch(self, pages: int = 2) -> list[Item]:
        items: dict[str, Item] = {}
        for media, kind in (("movie", "film"), ("tv", "serie")):
            # Le stesse liste per tutti: popolari, e popolari per bambini/famiglie.
            for extra in ({}, {"with_genres": "16|10751"}):
                for page in range(1, pages + 1):
                    data = self.get(self._url(f"/discover/{media}", watch_region=REGION, sort_by="popularity.desc",
                                              include_adult="false", page=page, **extra))
                    for r in data.get("results", []):
                        item = Item(
                            f"tmdb:{media}:{r['id']}", kind, r.get("title") or r.get("name") or "",
                            [GENRES[g] for g in r.get("genre_ids", []) if g in GENRES],
                            int((r.get("release_date") or r.get("first_air_date") or "0")[:4] or 0),
                            float(r.get("popularity") or 0), (r.get("overview") or "")[:300], adult=bool(r.get("adult")),
                            poster=f"https://image.tmdb.org/t/p/w185{r['poster_path']}" if r.get("poster_path") else "")
                        items.setdefault(item.id, item)
        # Serie con episodi in onda: lista generica; il confronto con le serie piaciute è locale.
        try:
            for r in self.get(self._url("/tv/on_the_air", page=1)).get("results", []):
                key = f"tmdb:tv:{r['id']}"
                item = items.setdefault(key, Item(key, "serie", r.get("name") or "",
                                                  [GENRES[g] for g in r.get("genre_ids", []) if g in GENRES],
                                                  int((r.get("first_air_date") or "0")[:4] or 0), float(r.get("popularity") or 0),
                                                  (r.get("overview") or "")[:300]))
                item.new_season = "in onda"
        except Exception:
            pass
        for item in items.values():
            media, tid = item.id.split(":")[1:]
            try:
                prov = self.get(self._url(f"/{media}/{tid}/watch/providers")).get("results", {}).get(REGION, {})
            except Exception:
                continue
            names = [p.get("provider_name", "") for p in prov.get("flatrate", []) + prov.get("free", []) + prov.get("ads", [])]
            item.providers = sorted({k for k in map(_provider_key, names) if k})
        return list(items.values())


class FlathubCatalog:
    """App e giochi popolari su Flathub (nessuna chiave richiesta)."""

    base = "https://flathub.org/api/v2"

    def __init__(self, get: Callable[[str], Any] = _get, base: str | None = None):
        self.get = get
        self.base = base or self.base

    @staticmethod
    def _hits(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return data
        return data.get("hits") or data.get("results") or data.get("apps") or []

    def fetch(self) -> list[Item]:
        items: dict[str, Item] = {}
        sources = [("/popular/last-month", None), ("/trending/last-two-weeks", None), ("/collection/category/Game", "gioco")]
        for path, forced in sources:
            try:
                hits = self._hits(self.get(self.base + path))
            except Exception:
                continue
            for n, h in enumerate(hits):
                app_id = h.get("app_id") or h.get("id") or h.get("flatpakAppId")
                if not app_id:
                    continue
                cats = [c.lower() for c in (h.get("main_categories") or h.get("categories") or [])]
                cats = cats if isinstance(cats, list) else [str(cats)]
                kind = forced or ("gioco" if "game" in cats else "software")
                items.setdefault(app_id, Item(f"flathub:{app_id}", kind, h.get("name") or app_id, cats,
                                              popularity=100.0 / (n + 1), overview=(h.get("summary") or "")[:200],
                                              poster=str(h.get("icon") or "")))
        return list(items.values())


class Catalog:
    """Cache locale dei cataloghi: i consigli funzionano anche offline."""

    def __init__(self, path: Path | None = None):
        self.path = path or cache_dir() / "catalog.json"
        try:
            raw = json.loads(self.path.read_text())
            self.items = [Item(**i) for i in raw["items"]]
            self.updated = raw.get("updated", 0)
        except (OSError, ValueError, KeyError, TypeError):
            self.items, self.updated = [], 0

    def replace(self, kinds: Iterable[str], items: list[Item]) -> None:
        kinds = set(kinds)
        self.items = [i for i in self.items if i.kind not in kinds] + items
        self.updated = time.time()
        self.path.write_text(json.dumps({"updated": self.updated, "items": [asdict(i) for i in self.items]}, ensure_ascii=False))

    def stale(self, max_age: float = 24 * 3600) -> bool:
        return time.time() - self.updated > max_age


def refresh_catalog(catalog: Catalog, tmdb: TMDBCatalog | None = None, flathub: FlathubCatalog | None = None) -> str:
    done = []
    key = tmdb_key()
    tmdb = tmdb or (TMDBCatalog(key) if key else None)
    if tmdb is not None:
        catalog.replace(("film", "serie"), tmdb.fetch())
        done.append("film e serie")
    flathub = flathub or FlathubCatalog()
    apps = flathub.fetch()
    if apps:
        catalog.replace(("software", "gioco"), apps)
        done.append("app e giochi")
    return ", ".join(done) or "niente (catalogo non raggiungibile)"


# --- profilo dei gusti ------------------------------------------------------------------


@dataclass
class Taste:
    liked: dict[str, list[str]] = field(default_factory=dict)  # titolo -> generi
    disliked: dict[str, list[str]] = field(default_factory=dict)
    seen: list[str] = field(default_factory=list)  # id già proposti e scartati/visti
    only_included: bool = True  # solo titoli inclusi negli abbonamenti (o gratis)

    def weights(self) -> dict[str, float]:
        w: dict[str, float] = {}
        for genres in self.liked.values():
            for g in genres:
                w[g] = w.get(g, 0) + 1.0
        for genres in self.disliked.values():
            for g in genres:
                w[g] = w.get(g, 0) - 0.7
        return w


class Profile:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "taste.json"
        try:
            self.taste = Taste(**json.loads(self.path.read_text()))
        except (OSError, ValueError, TypeError):
            self.taste = Taste()

    def save(self) -> None:
        self.path.write_text(json.dumps(asdict(self.taste), ensure_ascii=False, indent=1))
        os.chmod(self.path, 0o600)

    def rate(self, title: str, liked: bool, genres: list[str]) -> None:
        target, other = (self.taste.liked, self.taste.disliked) if liked else (self.taste.disliked, self.taste.liked)
        other.pop(title, None)
        target[title] = genres
        self.save()


# --- scelta ------------------------------------------------------------------------------


@dataclass
class Pick:
    item: Item
    score: float
    why: str
    where: str


def _norm(t: str) -> str:
    return re.sub(r"[^\w]", "", t.lower())


def recommend(catalog: Catalog, profile: Profile, subs: Subscriptions, kind: str, request: str = "",
              limit: int = 3) -> list[Pick]:
    request = request.lower()
    kids = bool(re.search(r"bambin|figli|ragazz|famiglia|kids|children|piccol", request)) or kind == "cartone"
    taste = profile.taste
    weights = taste.weights()
    available = subs.available_services()
    names = {s.key: s.name for s in SERVICES}
    free = {s.key for s in SERVICES if s.free}
    wanted = {"cartone": ("film", "serie")}.get(kind, (kind,))
    liked_titles = {_norm(t) for t in taste.liked}
    hidden = {_norm(t) for t in taste.disliked} | set(taste.seen)
    words = [w for w in re.findall(r"\w+", request) if len(w) > 3]

    picks = []
    for item in catalog.items:
        if item.kind not in wanted or item.adult or item.id in hidden or _norm(item.title) in hidden:
            continue
        if _norm(item.title) in liked_titles and not item.new_season:
            continue  # già visto e piaciuto: non lo si ripropone
        if kind == "cartone" and not item.animated:
            continue
        if kids and (KIDS_NO & set(item.genres) or not KIDS_OK & set(item.genres)):
            continue
        if item.kind in ("film", "serie"):
            included = [p for p in item.providers if p in available]
            if taste.only_included and not included:
                continue
            where = names[included[0]] + (" · gratis" if included[0] in free else " · già nel tuo abbonamento") if included \
                else "a pagamento"
        else:
            where = "Flathub · gratis"
        affinity = sum(weights.get(g, 0) for g in item.genres)
        score = affinity * 2 + math.log1p(item.popularity)
        why = "popolare in questo periodo"
        if affinity > 0:
            fav = max(item.genres, key=lambda g: weights.get(g, 0))
            source = next((t for t, g in taste.liked.items() if fav in g), "")
            why = f"perché ti è piaciuto «{source}»" if source else f"ti piace il genere {fav}"
        if item.new_season and _norm(item.title) in liked_titles:
            score += 20
            why = "nuova stagione di una serie che ti è piaciuta"
        if words and any(w in f"{item.title} {item.overview} {' '.join(item.genres)}".lower() for w in words):
            score += 3
        if item.year >= date.today().year - 1:
            score += 1
        picks.append(Pick(item, score, why, where))
    picks.sort(key=lambda p: p.score, reverse=True)
    return picks[:limit]
