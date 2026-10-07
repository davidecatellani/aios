#!/usr/bin/env python3
"""Controlla il contenuto target-files della GSI prima di estrarre un'immagine da distribuire."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile


class InvalidGsi(ValueError):
    pass


def verify(path, expected_sdk=36):
    required = (
        "IMAGES/system.img", "IMAGES/vbmeta.img",
        "SYSTEM/system_ext/priv-app/Nova/Nova.apk",
        "SYSTEM/system_ext/bin/aios-llama-server",
        "SYSTEM/system_ext/etc/permissions/privapp-permissions-org.aios.nova.xml",
        "SYSTEM/build.prop",
        "SYSTEM/system_ext/bin/aios-whisper",
        "SYSTEM/system_ext/bin/aios-espeak",
        "SYSTEM/system_ext/etc/aios/models/nova.gguf",
        "SYSTEM/system_ext/etc/aios/models/whisper.bin",
        "SYSTEM/system_ext/etc/aios/models/voice-data.zip",
        "SYSTEM/system_ext/etc/aios/models/manifest.json",
        "SYSTEM/system_ext/app/SoiaFDroid/SoiaFDroid.apk",
        "SYSTEM/system_ext/app/SoiaKDEConnect/SoiaKDEConnect.apk",
        "SYSTEM/product/priv-app/GmsCore/GmsCore.apk",
        "SYSTEM/product/priv-app/Phonesky/Phonesky.apk",
        "SYSTEM/system_ext/priv-app/GoogleServicesFramework/GoogleServicesFramework.apk",
        "SYSTEM/product/etc/permissions/privapp-permissions-google-product.xml",
        "SYSTEM/system_ext/etc/permissions/privapp-permissions-google-system-ext.xml",
    )
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        missing = [name for name in required if name not in names or archive.getinfo(name).file_size == 0]
        if missing:
            raise InvalidGsi("Contenuto GSI incompleto: " + ", ".join(missing))
        for name in (required[4], required[5], required[11]):
            if archive.getinfo(name).file_size > 1024 * 1024:
                raise InvalidGsi(f"Metadati troppo grandi: {name}")
        props = dict(line.split("=", 1) for line in archive.read("SYSTEM/build.prop").decode().splitlines()
                     if "=" in line and not line.startswith("#"))
        sdk = props.get("ro.build.version.sdk") or props.get("ro.system.build.version.sdk")
        if sdk != str(expected_sdk):
            raise InvalidGsi(f"SDK dell'immagine {sdk!r}, atteso {expected_sdk}")
        permissions = ET.fromstring(archive.read(required[4]))
        nova = permissions.find("privapp-permissions[@package='org.aios.nova']")
        if nova is None or "android.permission.TETHER_PRIVILEGED" not in {
                entry.get("name") for entry in nova.findall("permission")}:
            raise InvalidGsi("Permessi privilegiati di Nova assenti o non validi")
        bundle = json.loads(archive.read(required[11]))
        if bundle.get("version") != 1:
            raise InvalidGsi("Manifest offline non valido")
        specs = {item["name"]: item for item in bundle["artifacts"]}
        packaged = {"nova.gguf": required[8], "whisper.bin": required[9], "voice-data.zip": required[10],
                    "FDroid.apk": required[12], "KDEConnect.apk": required[13]}
        for name, location in packaged.items():
            item = specs.get(name)
            if not item or item["size"] != archive.getinfo(location).file_size:
                raise InvalidGsi(f"Dimensione offline non valida: {name}")
            with archive.open(location) as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != item["sha256"]:
                raise InvalidGsi(f"Impronta offline non valida: {name}")
    return {"sdk": expected_sdk, "nova_in_system": True, "server_nativo_presente": True, "permessi_nova_presenti": True,
            "contenuto_offline_verificato": True, "google_incluso": True, "compatibilita_hardware_verificata": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_files", type=Path)
    parser.add_argument("--sdk", type=int, default=36)
    args = parser.parse_args(argv)
    try:
        result = verify(args.target_files, args.sdk)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, UnicodeError, ET.ParseError) as exc:
        print(f"GSI rifiutata: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
