"""Nuove versioni di SoIA senza registro pubblico: da GitHub (con il tuo accesso) o da una chiavetta.

Ogni versione dell'immagine è pubblicata anche come pacchetto (Release del repository, procedura
«Immagine SoIA»):

    aios-aggiornamento.json                 versione, Fedora, sha256 e pezzi
    aios-aggiornamento.ociarchive.parte0…N  l'immagine in pezzi da meno di 2 GB

- **Da GitHub**: il PC li scarica dalle Release del repository. Se il repository è pubblico non serve
  nulla; se è privato, con un permesso di sola lettura dell'utente (token GitHub «fine-grained», solo
  questo repository, solo «Contents: read»), conservato nel portachiavi. Lo scaricamento riprende da dove
  si era fermato.
- **Da chiavetta**: gli stessi file copiati su una chiavetta (anche FAT32: i pezzi sono piccoli).
  Inserita la chiavetta, Nova lo nota e chiede se installare.

In entrambi i casi ogni pezzo e il file ricomposto si verificano con sha256, poi il sistema nuovo
si prepara accanto a quello in uso (rpm-ostree/bootc) e parte al riavvio: dati, impostazioni e app
restano, la versione precedente pure. Niente formattazione.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

MANIFEST = "aios-aggiornamento.json"
ARCHIVE = "aios-aggiornamento.ociarchive"
VERSION_FILE = Path("/usr/share/aios/versione")
VARIANT_FILE = Path("/usr/share/aios/variante")  # «universale» (per tutti), o le vecchie «standard» e «nvidia»
CONFIG_FILE = Path("/usr/share/aios/aggiornamenti.json")
TOKEN_KEY = "github-aggiornamenti"
CHUNK = 1 << 20


def workdir() -> Path:
    return Path(os.environ.get("AIOS_AGGIORNAMENTI_DIR", "/var/tmp/aios-aggiornamento"))


def installed_variant(path: Path = VARIANT_FILE) -> str:
    try:
        return path.read_text().strip() or "standard"
    except OSError:
        return "standard"


def same_variant(manifest: dict[str, Any], variant: str | None = None) -> bool:
    """Un pacchetto della variante giusta. L'immagine universale va bene per tutti (il driver NVIDIA si attiva solo
    dove c'è la scheda); delle vecchie varianti, un PC NVIDIA non deve prendere l'immagine senza driver."""
    offered = str(manifest.get("variante") or "standard")
    mine = variant or installed_variant()
    if offered == "universale":
        return True
    if mine == "universale":
        return offered == "nvidia"  # ha il driver: non si torna a un'immagine senza
    return offered == mine


def installed_version(path: Path = VERSION_FILE) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def configured_repo(path: Path = CONFIG_FILE) -> str:
    try:
        return str(json.loads(path.read_text()).get("repo", ""))
    except (OSError, ValueError):
        return ""


def version_key(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else -1 for p in v.replace("-", ".").split("."))


def newer(candidate: str, current: str) -> bool:
    return bool(candidate) and (not current or version_key(candidate) > version_key(current))


@dataclass
class Part:
    name: str
    sha256: str
    size: int


@dataclass
class Package:
    version: str
    sha256: str
    parts: list[Part]
    origin: str  # «GitHub» | «chiavetta»
    fetch: Callable[[Part, Path], None]  # porta un pezzo nella cartella di lavoro (riprendendo se c'è già)
    fedora: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return sum(p.size for p in self.parts)


def parse_manifest(data: dict[str, Any]) -> tuple[str, str, list[Part], str]:
    parts = [Part(str(p["nome"]), str(p["sha256"]), int(p["dimensione"])) for p in data.get("parti", [])]
    for p in parts:
        if "/" in p.name or p.name.startswith(".") or not p.name.startswith(ARCHIVE):
            raise ValueError(f"nome di pezzo non valido: {p.name}")
    if not parts or not data.get("versione") or len(str(data.get("sha256", ""))) != 64:
        raise ValueError("descrizione dell'aggiornamento incompleta")
    return str(data["versione"]), str(data["sha256"]), parts, str(data.get("fedora", ""))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def assemble(pkg: Package, work: Path | None = None, progress: Callable[[str], None] = lambda s: None) -> Path:
    """Porta i pezzi, li verifica e ricompone l'immagine. → percorso del file pronto."""
    work = work or workdir()
    work.mkdir(parents=True, exist_ok=True)
    ready = work / ARCHIVE
    if ready.exists() and ready.stat().st_size == pkg.size and sha256_file(ready) == pkg.sha256:
        return ready  # già scaricata e ricomposta (es. preparazione non riuscita la volta prima)
    free = shutil.disk_usage(work).free
    have = sum((work / p.name).stat().st_size for p in pkg.parts if (work / p.name).exists())
    if free < (pkg.size - have) + pkg.size + (1 << 30):
        raise OSError(f"spazio insufficiente: servono circa {round((2 * pkg.size - have) / 1e9 + 1)} GB liberi")
    for i, part in enumerate(pkg.parts, 1):
        dest = work / part.name
        if not (dest.exists() and dest.stat().st_size == part.size and sha256_file(dest) == part.sha256):
            progress(f"pezzo {i} di {len(pkg.parts)} ({pkg.origin})")
            pkg.fetch(part, dest)
            if dest.stat().st_size < part.size:  # interrotto: si riprende da qui la prossima volta
                raise OSError(f"scaricamento interrotto al pezzo {i}: riprendo da lì la prossima volta")
            if sha256_file(dest) != part.sha256:
                dest.unlink(missing_ok=True)
                raise ValueError(f"il pezzo {part.name} è rovinato: lo riscarico la prossima volta")
    archive = work / ARCHIVE
    h = hashlib.sha256()
    with open(archive, "wb") as out:
        for part in pkg.parts:
            with open(work / part.name, "rb") as f:
                for block in iter(lambda: f.read(CHUNK), b""):
                    h.update(block)
                    out.write(block)
    if h.hexdigest() != pkg.sha256:
        archive.unlink(missing_ok=True)
        raise ValueError("l'immagine ricomposta non corrisponde: non la installo")
    for part in pkg.parts:
        (work / part.name).unlink(missing_ok=True)
    return archive


def stage_command(tool: str, archive: Path) -> list[str]:
    """Prepara il sistema nuovo dall'immagine nel file (quello in uso non cambia)."""
    if tool == "bootc":
        return ["bootc", "switch", "--transport", "oci-archive", str(archive)]
    return ["rpm-ostree", "rebase", f"ostree-unverified-image:oci-archive:{archive}"]


# --- GitHub --------------------------------------------------------------------------------------------
class GithubSource:
    API = "https://api.github.com"

    def __init__(self, repo: str, token: str = "", opener: Callable[[urllib.request.Request], Any] | None = None):
        self.repo, self.token = repo, (token or "").strip()  # senza token: repository pubblico
        self.variant: str | None = None  # None: quella installata
        self.open = opener or (lambda req: urllib.request.urlopen(req, timeout=60))

    def _request(self, url: str, accept: str = "application/vnd.github+json",
                 headers: dict[str, str] | None = None) -> urllib.request.Request:
        req = urllib.request.Request(url, headers={"Accept": accept, "X-GitHub-Api-Version": "2022-11-28",
                                                   "User-Agent": "AIOS", **(headers or {})})
        # il token va solo a GitHub, non al server dei file a cui GitHub poi rimanda
        if self.token:
            req.add_unredirected_header("Authorization", f"Bearer {self.token}")
        return req

    def _json(self, path: str) -> Any:
        with self.open(self._request(self.API + path)) as resp:
            return json.loads(resp.read())

    def check_access(self) -> str:
        """→ "" se il permesso funziona, altrimenti il motivo."""
        try:
            self._json(f"/repos/{self.repo}")
            return ""
        except urllib.error.HTTPError as exc:
            if not self.token:
                return {404: f"il repository {self.repo} non è pubblico: serve un token"}.get(exc.code, f"errore {exc.code}")
            return {401: "il token non è valido o è scaduto", 403: "il token non ha il permesso di leggere",
                    404: f"il token non vede il repository {self.repo}"}.get(exc.code, f"errore {exc.code}")
        except (OSError, ValueError) as exc:
            return f"GitHub non raggiungibile ({exc})"

    def latest(self) -> Package | None:
        for release in self._json(f"/repos/{self.repo}/releases?per_page=10"):
            if release.get("draft"):
                continue
            assets = {a["name"]: a for a in release.get("assets", [])}
            if MANIFEST not in assets:
                continue
            with self.open(self._request(assets[MANIFEST]["url"], "application/octet-stream")) as resp:
                manifest = json.loads(resp.read())
            if not same_variant(manifest, self.variant):
                continue
            version, sha, parts, fedora = parse_manifest(manifest)
            if any(p.name not in assets for p in parts):
                continue

            def fetch(part: Part, dest: Path, assets: dict = assets) -> None:
                self._download(assets[part.name]["url"], dest, part.size)

            return Package(version, sha, parts, "GitHub", fetch, fedora, {"release": release.get("tag_name", "")})
        return None

    def _download(self, url: str, dest: Path, size: int) -> None:
        have = dest.stat().st_size if dest.exists() else 0
        if have > size:
            dest.unlink()
            have = 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        with self.open(self._request(url, "application/octet-stream", headers)) as resp:
            mode = "ab" if have and getattr(resp, "status", 200) == 206 else "wb"
            with open(dest, mode) as out:
                for block in iter(lambda: resp.read(CHUNK), b""):
                    out.write(block)


# --- chiavetta -----------------------------------------------------------------------------------------
def media_roots() -> list[Path]:
    user = os.environ.get("USER") or Path.home().name
    roots = []
    for base in (Path("/run/media") / user, Path("/media") / user, Path("/media")):
        try:
            roots += [p for p in base.iterdir() if p.is_dir()]
        except OSError:
            pass
    return roots


def find_on_media(roots: list[Path] | None = None) -> Package | None:
    for root in roots if roots is not None else media_roots():
        for folder in (root, *sorted(p for p in root.iterdir() if p.is_dir())) if root.is_dir() else ():
            manifest = folder / MANIFEST
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text())
                if not same_variant(data):
                    continue
                version, sha, parts, fedora = parse_manifest(data)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if all((folder / p.name).is_file() for p in parts):

                def fetch(part: Part, dest: Path, folder: Path = folder) -> None:
                    shutil.copyfile(folder / part.name, dest)

                return Package(version, sha, parts, "chiavetta", fetch, fedora, {"cartella": str(folder)})
    return None
