#!/usr/bin/env python3
"""Raccoglie dati di compatibilità via ADB. Non sblocca, riavvia o scrive sul telefono."""

import argparse
import json
import re
import subprocess
import sys


PROPERTIES = (
    "ro.product.manufacturer", "ro.product.model", "ro.product.device", "ro.product.cpu.abi",
    "ro.build.version.release", "ro.build.version.sdk", "ro.vendor.build.version.sdk",
    "ro.product.first_api_level", "ro.treble.enabled", "ro.vndk.version",
    "ro.boot.dynamic_partitions", "ro.boot.slot_suffix", "ro.boot.flash.locked",
    "ro.boot.vbmeta.device_state", "sys.oem_unlock_allowed", "ro.boot.verifiedbootstate",
    "ro.build.version.security_patch", "ro.vendor.build.security_patch",
)


def assess(props, min_sdk=36, max_sdk=36):
    """Separare candidabilità GSI da compatibilità reale, che richiede una prova hardware."""
    model = props.get("ro.product.model", "")
    brand = props.get("ro.product.manufacturer", "")
    target = brand.lower() in ("motorola", "lenovo") and bool(
        re.fullmatch(r"(?:(?:motorola\s+)?edge\s+50\s+neo|XT2409-1)", model.strip(), re.I))
    blockers = []
    if props.get("ro.treble.enabled") != "true":
        blockers.append("Supporto Treble non confermato dalle proprietà del dispositivo.")
    if props.get("ro.product.cpu.abi") != "arm64-v8a":
        blockers.append("ABI arm64-v8a non confermata: non scegliere la GSI arm64.")
    if not props.get("ro.build.version.sdk", "").isdigit():
        blockers.append("Versione SDK Android non rilevata.")
    elif not min_sdk <= int(props["ro.build.version.sdk"]) <= max_sdk:
        blockers.append(f"Questa variante GSI richiede SDK {min_sdk}–{max_sdk}; la versione Android rilevata è diversa.")
    oem = {"1": True, "0": False}.get(props.get("sys.oem_unlock_allowed"))
    unlocked_values = [value for value in (
        {"0": True, "1": False}.get(props.get("ro.boot.flash.locked")),
        {"unlocked": True, "locked": False}.get(props.get("ro.boot.vbmeta.device_state")),
    ) if value is not None]
    unlocked = unlocked_values[0] if unlocked_values and len(set(unlocked_values)) == 1 else None
    install_blockers = list(blockers)
    if unlocked is not True:
        install_blockers.append("Bootloader ancora bloccato o stato non accertato. Sblocco OEM attivato non significa bootloader sbloccato.")
    return {
        "proprieta": {key: props.get(key, "") for key in PROPERTIES},
        "edge_50_neo_riconosciuto": target,
        "prerequisiti_gsi_rilevati": not blockers,
        "compatibilita_soia_verificata": False,
        "bootloader": {"sblocco_oem_attivato": oem, "sbloccato": unlocked},
        "blocchi": blockers,
        "blocchi_installazione": install_blockers,
        "da_verificare": [
            "Idoneità allo sblocco del preciso modello XT e della variante operatore sul portale Motorola.",
            "Base GSI Android 16 coerente con il firmware vendor; compatibilità VINTF da verificare.",
            "Partizioni dinamiche, spazio di system, percorso fastbootd e immagini stock di ripristino.",
            "Avvio, modem/chiamate, fotocamera, impronta, NFC, Wi-Fi, Bluetooth e consumo a riposo.",
        ],
    }


def collect(adb, serial=None):
    def call(*args):
        result = subprocess.run([adb, *args], check=True, capture_output=True, text=True, timeout=15)
        return result.stdout.strip()

    rows = [line.split() for line in call("devices").splitlines()[1:] if line.strip()]
    authorized = [row[0] for row in rows if len(row) >= 2 and row[1] == "device"]
    if serial is None:
        if len(authorized) != 1:
            raise ValueError("Collega un solo telefono con Debug USB autorizzato, oppure indica --serial.")
        serial = authorized[0]
    elif serial not in authorized:
        raise ValueError("Il telefono selezionato non è collegato e autorizzato via ADB.")
    # Solo proprietà esplicite: non esportare identificativi, account o l'intero getprop.
    return {key: call("-s", serial, "shell", "getprop", key) for key in PROPERTIES}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb", help="Percorso di adb")
    parser.add_argument("--serial", help="Seleziona un telefono se ne è collegato più di uno (non incluso nel report)")
    args = parser.parse_args(argv)
    try:
        from pathlib import Path
        target = json.loads((Path(__file__).resolve().parents[1] / "dispositivi.json").read_text())["bersagli"]["gsi"]
        report = assess(collect(args.adb, args.serial), int(target["min_sdk"]), int(target["max_sdk"]))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Verifica non eseguita: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
