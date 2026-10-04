"""Aggiornamenti incrementali: AIOS dal registro delle immagini di GitHub (ghcr.io).

La costruzione (immagine.yml) pubblica l'immagine divisa in strati stabili. Un PC che si aggiorna dal
registro scarica solo gli strati cambiati (di solito qualche centinaio di MB) invece del pacchetto
completo della Release (~8 GB). Il repository è privato, quindi serve un accesso: lo stesso token
già dato ad AIOS per gli aggiornamenti, se può leggere i pacchetti (token «classico» con read:packages;
i token «fine-grained» il registro non li accetta).

Passi: 1) si prova il token col registro; 2) l'accesso si lascia in /var/lib/aios-registro, dove un
servizio di sistema (aios-credenziali-registro) lo sposta in /etc/ostree/auth.json, leggibile solo da
root; 3) al prossimo aggiornamento il sistema passa al registro (`rpm-ostree rebase`): quella volta
scarica tutto, poi solo le differenze (`rpm-ostree upgrade`).
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

REGISTRY = "ghcr.io"
DROP = Path("/var/lib/aios-registro/auth.json")
MANIFEST_TYPES = ", ".join([
    "application/vnd.oci.image.index.v1+json", "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json", "application/vnd.docker.distribution.manifest.list.v2+json"])


def fedora_version(os_release: Path = Path("/etc/os-release")) -> str:
    try:
        for line in os_release.read_text().splitlines():
            if line.startswith("VERSION_ID="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "44"


def image_ref(repo: str, fedora: str | None = None) -> str:
    return f"{REGISTRY}/{repo.lower()}:{fedora or fedora_version()}"


def rebase_command(tool: str, ref: str) -> list[str]:
    if tool == "bootc":
        return ["bootc", "switch", ref]
    return ["rpm-ostree", "rebase", f"ostree-unverified-registry:{ref}"]


def on_registry(status_json: str, ref: str) -> bool:
    """Il sistema in uso (o quello pronto) viene già dal registro?"""
    try:
        deployments = json.loads(status_json).get("deployments", [])
    except ValueError:
        return False
    return any(ref in str(d.get("container-image-reference", "")) for d in deployments if d.get("booted") or d.get("staged"))


class Access:
    """Prova il token col registro di GitHub (sola lettura) e prepara l'accesso per il sistema."""

    def __init__(self, repo: str, token: str, opener: Callable[[urllib.request.Request], Any] | None = None):
        self.repo, self.token = repo.lower(), token.strip()
        self.open = opener or (lambda req: urllib.request.urlopen(req, timeout=30))
        self.login = ""

    def _get(self, url: str, headers: dict[str, str]) -> Any:
        return self.open(urllib.request.Request(url, headers={"User-Agent": "AIOS", **headers}))

    def check(self, tag: str | None = None) -> str:
        """→ "" se il token può scaricare l'immagine di AIOS, altrimenti il motivo in parole semplici."""
        try:
            with self._get("https://api.github.com/user", {"Authorization": f"Bearer {self.token}",
                                                           "Accept": "application/vnd.github+json"}) as resp:
                self.login = json.loads(resp.read()).get("login", "")
            basic = base64.b64encode(f"{self.login}:{self.token}".encode()).decode()
            with self._get(f"https://{REGISTRY}/token?service={REGISTRY}&scope=repository:{self.repo}:pull",
                           {"Authorization": f"Basic {basic}"}) as resp:
                bearer = json.loads(resp.read()).get("token", "")
            ref = image_ref(self.repo, tag)
            name, _, version = ref.removeprefix(f"{REGISTRY}/").partition(":")
            with self._get(f"https://{REGISTRY}/v2/{name}/manifests/{version}",
                           {"Authorization": f"Bearer {bearer}", "Accept": MANIFEST_TYPES}):
                return ""
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return ("il token non può leggere il registro delle immagini: serve un token «classico» con il "
                        "permesso read:packages (i token «fine-grained» il registro non li accetta)")
            if exc.code == 404:
                return "nel registro non c'è ancora l'immagine di AIOS (arriva con la prossima anteprima)"
            return f"errore {exc.code} dal registro"
        except (OSError, ValueError) as exc:
            return f"registro non raggiungibile ({exc})"

    def auth_json(self) -> dict[str, Any]:
        basic = base64.b64encode(f"{self.login or 'aios'}:{self.token}".encode()).decode()
        return {"auths": {REGISTRY: {"auth": basic}}}

    def hand_over(self, drop: Path = DROP, wait: float = 15.0, sleep: Callable[[float], None] = time.sleep) -> bool:
        """Lascia l'accesso al servizio di sistema e aspetta che lo prenda (il file sparisce)."""
        if not drop.parent.is_dir():
            return False
        tmp = drop.with_name(".auth.json.nuovo")
        tmp.write_text(json.dumps(self.auth_json()))
        tmp.chmod(0o600)
        tmp.replace(drop)  # il servizio parte quando compare il file completo
        waited = 0.0
        while drop.exists() and waited < wait:
            sleep(0.5)
            waited += 0.5
        return not drop.exists()
