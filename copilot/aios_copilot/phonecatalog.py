"""Catalogo firmato delle immagini di SoIA per telefono (lo legge l'installatore, phoneinstall.py).

Dopo scripts/firma.sh:

    aios-catalogo-telefoni --bersaglio miatoll --cartella phone/uscita/miatoll \\
        --url https://download.aios.example/0.1/miatoll --chiave ~/.aios-chiavi/catalogo.pem \\
        --catalogo telefoni.json

aggiunge (o sostituisce) la voce del bersaglio, calcola le impronte SHA-256 dei file e
firma il catalogo (telefoni.json.sig), con la stessa chiave Ed25519 del catalogo dei modelli.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .modelcatalog import sign_file

PARTITIONS = {"system.img": "system", "vbmeta.img": "vbmeta", "recovery.img": "recovery", "boot.img": "recovery"}
DEFAULT_TARGETS = Path(__file__).resolve().parents[2] / "phone" / "dispositivi.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def entries_for(target: str, spec: dict, folder: Path, url: str, version: str) -> list[dict]:
    """Le voci del catalogo per un bersaglio: sistema (e recovery, se c'è)."""
    url = url.rstrip("/")
    item = lambda f: {"nome": f.name, "url": f"{url}/{f.name}", "sha256": sha256(f),  # noqa: E731
                      **({"partizione": PARTITIONS[f.name]} if f.name in PARTITIONS else {})}
    outputs = [folder / name for name in spec.get("uscite", [])]
    missing = [p.name for p in outputs if not p.exists()]
    if missing:
        raise SystemExit(f"Mancano in {folder}: {', '.join(missing)} (lancia prima scripts/firma.sh {target})")
    codename = spec.get("dispositivo_lineage") or target
    brand = spec.get("marca", "")
    system_files = [item(p) for p in outputs if p.name not in ("recovery.img", "boot.img", "avb_pkmd.bin")]
    entry = {"nome": spec["nome"], "versione": version, "tipo": spec["tipo_catalogo"], "marca": brand,
             "codename": "" if spec["tipo_catalogo"] == "gsi" else codename, "codici": spec.get("codici", []),
             "abi": "arm64-v8a", "file": system_files}
    if (folder / "avb_pkmd.bin").exists():
        entry["chiave_avb"] = item(folder / "avb_pkmd.bin")
    if spec["tipo_catalogo"] == "gsi":
        entry["min_sdk"] = int(spec.get("min_sdk", 30))
        if spec.get("max_sdk"):
            entry["max_sdk"] = int(spec["max_sdk"])
            if entry["max_sdk"] < entry["min_sdk"]:
                raise SystemExit("Intervallo SDK della GSI non valido")
        if spec.get("android_sdk"):
            entry["android_sdk"] = int(spec["android_sdk"])
        entry["source_ref"] = spec.get("ramo", "")
    entries = [entry]
    recovery = [p for p in outputs if p.name in ("recovery.img", "boot.img")]
    if recovery:
        entries.append({"nome": f"Recovery di SoIA ({codename})", "versione": version, "tipo": "recovery", "marca": brand,
                        "codename": codename, "codici": spec.get("codici", []), "file": [item(p) for p in recovery]})
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aios-catalogo-telefoni", description=__doc__.splitlines()[0])
    parser.add_argument("--bersaglio", required=True)
    parser.add_argument("--cartella", type=Path, required=True)
    parser.add_argument("--url", required=True, help="indirizzo https da cui si scaricano i file")
    parser.add_argument("--chiave", type=Path, required=True, help="chiave Ed25519 del catalogo (PEM)")
    parser.add_argument("--catalogo", type=Path, default=Path("telefoni.json"))
    parser.add_argument("--versione", default="0.1")
    parser.add_argument("--dispositivi", type=Path, default=DEFAULT_TARGETS)
    args = parser.parse_args(argv)
    if not args.url.startswith("https://"):
        raise SystemExit("i file vanno pubblicati in https")
    spec = json.loads(args.dispositivi.read_text())["bersagli"][args.bersaglio]
    new = entries_for(args.bersaglio, spec, args.cartella, args.url, args.versione)
    try:
        doc = json.loads(args.catalogo.read_text())
    except (OSError, ValueError):
        doc = {"immagini": []}
    names = {e["nome"] for e in new}
    doc["immagini"] = [e for e in doc.get("immagini", []) if e.get("nome") not in names] + new
    args.catalogo.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    sig = sign_file(args.catalogo, args.chiave)
    print(f"Catalogo aggiornato: {args.catalogo} ({len(doc['immagini'])} immagini), firma {sig}. Pubblicali entrambi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
