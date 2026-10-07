"""La struttura di compilazione di SoIA per telefono (phone/) e il catalogo firmato delle immagini."""

import base64
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aios_copilot import modelcatalog, phonecatalog
from aios_copilot import phoneinstall as pi

PHONE = Path(__file__).resolve().parents[2] / "phone"


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def test_tree_is_well_formed():
    targets = json.loads((PHONE / "dispositivi.json").read_text())["bersagli"]
    assert {"gsi", "shiba", "miatoll"} <= set(targets)
    assert "M2003J6B2G" in targets["miatoll"]["modelli"] and targets["miatoll"]["base"] == "lineage"
    for xml in PHONE.rglob("*.xml"):
        ET.parse(xml)
    for script in (PHONE / "scripts").glob("*.sh"):
        subprocess.run(["bash", "-n", str(script)], check=True)
    for kt in (PHONE / "packages/apps/Nova/src").rglob("*.kt"):
        text = kt.read_text()
        assert text.startswith("package org.aios.nova") and text.count("{") == text.count("}"), kt
    manifest = ET.parse(PHONE / "packages/apps/Nova/AndroidManifest.xml").getroot()
    ns = "{http://schemas.android.com/apk/res/android}"
    declared = {e.get(ns + "name") for e in manifest.iter() if e.tag in ("activity", "service", "receiver")}
    sources = {str(p.relative_to(PHONE / "packages/apps/Nova/src/org/aios/nova")).replace("/", ".")[:-3]
               for p in (PHONE / "packages/apps/Nova/src").rglob("*.kt")}
    assert {d.lstrip(".") for d in declared} <= sources  # ogni componente dichiarato esiste
    assert not list(PHONE.rglob("*.pk8")) and not list(PHONE.rglob("*.pem"))  # mai chiavi nel repository


def test_catalog_from_signed_build_output(tmp_path):
    out = tmp_path / "uscita/miatoll"
    out.mkdir(parents=True)
    (out / "aios-miatoll.zip").write_bytes(b"ota di aios")
    (out / "recovery.img").write_bytes(b"recovery di aios")
    gsi = tmp_path / "uscita/gsi"
    gsi.mkdir(parents=True)
    (gsi / "system.img").write_bytes(b"system")
    (gsi / "vbmeta.img").write_bytes(b"vbmeta")
    pem = tmp_path / "catalogo.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(pem)], check=True, capture_output=True)
    catalog = tmp_path / "telefoni.json"
    for target, folder in (("miatoll", out), ("gsi", gsi)):
        phonecatalog.main(["--bersaglio", target, "--cartella", str(folder), "--url", f"https://dl.aios.example/0.1/{target}",
                           "--chiave", str(pem), "--catalogo", str(catalog), "--dispositivi", str(PHONE / "dispositivi.json")])
    doc = json.loads(catalog.read_text())
    assert [e["tipo"] for e in doc["immagini"]] == ["dispositivo", "recovery", "gsi"]

    # l'installatore lo accetta (firma verificata) e per il Redmi Note 9 Pro sceglie l'immagine dedicata
    served = {"https://c/t.json": catalog.read_bytes(), "https://c/t.json.sig": catalog.with_name("telefoni.json.sig").read_bytes()}
    key = base64.b64decode(modelcatalog.public_key(pem))
    assert pi.update_catalog("https://c/t.json", served.__getitem__, keys=[key]) == 3
    redmi = pi.PhoneInfo("adb", "x", "xiaomi", "M2003J6B2G", "joyeuse", 30, "arm64-v8a", True)  # come si presenta con MIUI
    system, recovery = pi.choose_build(redmi, pi.load_catalog())
    assert system.files[0]["nome"] == "aios-miatoll.zip" and recovery.files[0]["partizione"] == "recovery"
    moto = pi.PhoneInfo("adb", "y", "motorola", "moto g84", "bangkk", 34, "arm64-v8a", True)
    assert pi.choose_build(moto, pi.load_catalog())[0] is None  # questa variante è Android 16
    edge = pi.PhoneInfo("adb", "z", "motorola", "XT2409-1", "", 36, "arm64-v8a", True)
    assert pi.choose_build(edge, pi.load_catalog())[0].kind == "gsi"
    edge.sdk = 37
    assert pi.choose_build(edge, pi.load_catalog())[0] is None  # nessun downgrade automatico
    entry = doc["immagini"][-1]
    assert entry["source_ref"] == "android-16.0.0_r3" and entry["android_sdk"] == 36
    with pytest.raises(SystemExit, match="https"):
        phonecatalog.main(["--bersaglio", "gsi", "--cartella", str(gsi), "--url", "http://insicuro", "--chiave", str(pem),
                           "--catalogo", str(catalog), "--dispositivi", str(PHONE / "dispositivi.json")])
