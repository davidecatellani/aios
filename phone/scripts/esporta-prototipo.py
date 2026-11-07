#!/usr/bin/env python3
"""Estrae il prototipo controllato; chiavi di test AOSP, mai una release di produzione."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

module = importlib.util.spec_from_file_location("gsi", Path(__file__).with_name("verifica-gsi.py"))
gsi = importlib.util.module_from_spec(module)
module.loader.exec_module(gsi)

def export(source, output, sdk=36):
    report = gsi.verify(source, sdk)
    output.mkdir(parents=True, exist_ok=True)
    images = []
    with zipfile.ZipFile(source) as archive:
        for name in ("system.img", "vbmeta.img"):
            fd, temp = tempfile.mkstemp(prefix=".image-", dir=output)
            try:
                with os.fdopen(fd, "wb") as stream, archive.open("IMAGES/" + name) as image:
                    shutil.copyfileobj(image, stream, 1024**2)
                path = Path(temp)
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                images.append({"name": name, "size": path.stat().st_size, "sha256": digest})
                os.replace(temp, output / name)
            finally:
                Path(temp).unlink(missing_ok=True)
        report.update({"format": "soia-phone-prototype-v1", "production_release": False,
            "signing": "AOSP test keys — utilizzare solo sul telefono di prova già sbloccato",
            "source_ref": "android-16.0.0_r3", "images": images,
            "bundle": json.loads(archive.read("SYSTEM/system_ext/etc/aios/models/manifest.json"))})
    temporary = output / ".prototipo.tmp"
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    temporary.replace(output / "prototipo.json")
    return report

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_files", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--sdk", type=int, default=36)
    args = parser.parse_args()
    try:
        report = export(args.target_files, args.output, args.sdk)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"GSI rifiutata: {exc}\n")
    print(f"Prototipo estratto in {args.output}; SHA-256 in prototipo.json. Nessun flash eseguito.")
    print(f"Compatibilità hardware verificata: {report['compatibilita_hardware_verificata']}")

if __name__ == "__main__":
    main()
