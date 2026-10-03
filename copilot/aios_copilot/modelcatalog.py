"""Catalogo dei modelli aggiornabile, firmato dal progetto AIOS.

I modelli migliori cambiano di mese in mese: l'elenco non può stare nel codice.
Il progetto AIOS pubblica un catalogo (JSON) con una firma Ed25519; AIOS lo scarica
periodicamente e lo usa solo se:
- la firma è valida per una delle chiavi fidate (/etc/aios/catalog-keys.d/*.pub,
  ~/.config/aios/catalog-keys); senza chiavi configurate nessun catalogo remoto è
  accettato e vale quello integrato;
- la versione è più alta di quella in uso (nessun ritorno a cataloghi vecchi, che
  potrebbero proporre modelli con problemi noti);
- il contenuto è ben formato.

La richiesta è identica per tutti i dispositivi: il confronto con l'hardware è locale.

Pubblicazione (per chi mantiene il catalogo): `aios-catalogo firma catalogo.json chiave.pem`
produce catalogo.json.sig con OpenSSL; `aios-catalogo chiave chiave.pem` stampa la
chiave pubblica da distribuire.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable

from . import ed25519
from .models import BUILTIN, BUILTIN_VERSION, CAPABILITIES, Model
from .privacy import private_dir

DEFAULT_URL = ""  # indirizzo ufficiale del catalogo, da impostare quando il progetto lo pubblica
MAX_BYTES = 2 * 2**20
MODEL_FIELDS = {f.name for f in fields(Model)}


class CatalogError(ValueError):
    pass


def store_path() -> Path:
    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios") / "model-catalog.json"


def trusted_keys() -> list[bytes]:
    """Chiavi pubbliche Ed25519 fidate (32 byte, in base64, una per riga)."""
    sources = sorted(Path("/etc/aios/catalog-keys.d").glob("*.pub"))
    user = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "catalog-keys"
    if user.exists():
        sources.append(user)
    keys = []
    for path in sources:
        try:
            for line in path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    key = base64.b64decode(line)
                    if len(key) == 32:
                        keys.append(key)
        except (OSError, ValueError):
            continue
    return keys


def parse(data: bytes) -> tuple[int, tuple[Model, ...]]:
    try:
        doc = json.loads(data)
    except ValueError as exc:
        raise CatalogError(f"catalogo illeggibile: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("version"), int) or not isinstance(doc.get("models"), list):
        raise CatalogError("catalogo senza versione o senza modelli")
    models = []
    for raw in doc["models"]:
        if not isinstance(raw, dict) or not {"name", "capability", "size_gb", "ram_gb"} <= raw.keys():
            raise CatalogError(f"modello incompleto: {raw!r:.80}")
        if raw["capability"] not in CAPABILITIES:
            continue  # capacità che questa versione di AIOS non conosce: la si ignora
        entry = {k: v for k, v in raw.items() if k in MODEL_FIELDS}
        for key in ("urls", "sha256"):
            entry[key] = tuple(entry.get(key, ()))
        if entry.get("engine", "ollama") == "file":
            urls = entry.get("urls", ())
            if not urls or len(entry.get("sha256", ())) != len(urls) or not all(u.startswith("https://") for u in urls):
                raise CatalogError(f"{entry['name']}: i file vanno scaricati in https e con impronta sha256")
        models.append(Model(**entry))
    if not models:
        raise CatalogError("nessun modello utilizzabile")
    return doc["version"], tuple(models)


def verify(data: bytes, signature: bytes, keys: list[bytes] | None = None) -> bool:
    keys = trusted_keys() if keys is None else keys
    return any(ed25519.verify(k, data, signature) for k in keys)


def _load_stored(keys: list[bytes] | None = None) -> tuple[int, tuple[Model, ...]] | None:
    path = store_path()
    sig = path.with_suffix(".json.sig")
    try:
        data, signature = path.read_bytes(), base64.b64decode(sig.read_bytes())
    except (OSError, ValueError):
        return None
    if not verify(data, signature, keys):  # ricontrollata a ogni avvio: un file alterato non passa
        return None
    try:
        return parse(data)
    except CatalogError:
        return None


def active_models() -> tuple[Model, ...]:
    stored = _load_stored()
    if stored and stored[0] > BUILTIN_VERSION:
        return stored[1]
    return BUILTIN


def active_version() -> int:
    stored = _load_stored()
    return stored[0] if stored and stored[0] > BUILTIN_VERSION else BUILTIN_VERSION


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AIOS/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise CatalogError("catalogo troppo grande")
    return data


def catalog_url() -> str:
    try:
        cfg = json.loads((Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "models.json").read_text())
        return cfg.get("_catalog_url") or DEFAULT_URL
    except (OSError, ValueError, AttributeError):
        return DEFAULT_URL


def update(url: str | None = None, fetch: Callable[[str], bytes] = _get, keys: list[bytes] | None = None) -> str:
    """Scarica, verifica e installa un catalogo più recente. Restituisce un messaggio leggibile."""
    url = url or catalog_url()
    if not url:
        return "Nessun indirizzo di catalogo configurato: resta il catalogo integrato."
    keys = trusted_keys() if keys is None else keys
    if not keys:
        return "Nessuna chiave fidata configurata: per sicurezza non accetto cataloghi remoti."
    data = fetch(url)
    signature = base64.b64decode(fetch(url + ".sig"))
    if not verify(data, signature, keys):
        raise CatalogError("firma non valida: catalogo rifiutato")
    version, models = parse(data)
    current = active_version()
    if version <= current:
        return f"Catalogo già aggiornato (versione {current})."
    path = store_path()
    path.with_suffix(".json.tmp").write_bytes(data)
    path.with_suffix(".json.sig").write_bytes(base64.b64encode(signature))
    path.with_suffix(".json.tmp").replace(path)
    return f"Catalogo aggiornato alla versione {version}: {len(models)} modelli."


# --- strumenti per chi pubblica il catalogo ---------------------------------------------------


def sign_file(catalog: Path, private_key_pem: Path) -> Path:
    raw = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(private_key_pem), "-rawin", "-in", str(catalog)],
                         capture_output=True, check=True).stdout
    out = catalog.with_name(catalog.name + ".sig")
    out.write_bytes(base64.b64encode(raw))
    return out


def public_key(private_key_pem: Path) -> str:
    der = subprocess.run(["openssl", "pkey", "-in", str(private_key_pem), "-pubout", "-outform", "DER"],
                         capture_output=True, check=True).stdout
    return base64.b64encode(der[-32:]).decode()


def export_builtin() -> dict[str, Any]:
    """Il catalogo integrato in formato pubblicabile (punto di partenza per il laboratorio)."""
    out = []
    for m in BUILTIN:
        out.append({f.name: (list(getattr(m, f.name)) if isinstance(getattr(m, f.name), tuple) else getattr(m, f.name))
                    for f in fields(Model)})
    return {"version": BUILTIN_VERSION + 1, "models": out}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["aggiorna"]:
        try:
            print(update(args[1] if len(args) > 1 else None))
        except (CatalogError, OSError) as exc:
            print(f"Aggiornamento rifiutato: {exc}")
            return 1
    elif args[:1] == ["firma"] and len(args) == 3:
        print(f"Firma scritta in {sign_file(Path(args[1]), Path(args[2]))}")
    elif args[:1] == ["chiave"] and len(args) == 2:
        print(public_key(Path(args[1])))
    elif args[:1] == ["esporta"]:
        print(json.dumps(export_builtin(), indent=1, ensure_ascii=False))
    elif args[:1] in (["stato"], []):
        print(f"Catalogo in uso: versione {active_version()}, {len(active_models())} modelli; "
              f"chiavi fidate: {len(trusted_keys())}; indirizzo: {catalog_url() or 'non configurato'}")
    else:
        print("aios-catalogo [stato | aggiorna [url] | esporta | firma catalogo.json chiave.pem | chiave chiave.pem]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
