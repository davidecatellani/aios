"""Una GSI distribuibile deve contenere davvero Nova e il suo file di permessi XML."""

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest


path = Path(__file__).resolve().parents[2] / "phone/scripts/verifica-gsi.py"
spec = importlib.util.spec_from_file_location("gsi_check", path)
gsi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gsi)


def contents():
    data = {
        "IMAGES/system.img": b"system", "IMAGES/vbmeta.img": b"vbmeta",
        "SYSTEM/system_ext/priv-app/Nova/Nova.apk": b"apk",
        "SYSTEM/system_ext/bin/aios-llama-server": b"native executable",
        "SYSTEM/system_ext/etc/permissions/privapp-permissions-org.aios.nova.xml":
            b'<permissions><privapp-permissions package="org.aios.nova">'
            b'<permission name="android.permission.TETHER_PRIVILEGED"/></privapp-permissions></permissions>',
        "SYSTEM/build.prop": b"ro.build.version.sdk=36\n",
    }
    data.update({
        "SYSTEM/system_ext/bin/aios-whisper": b"synthetic native executable",
        "SYSTEM/system_ext/bin/aios-espeak": b"synthetic native executable",
        "SYSTEM/product/priv-app/GmsCore/GmsCore.apk": b"synthetic apk",
        "SYSTEM/product/priv-app/Phonesky/Phonesky.apk": b"synthetic apk",
        "SYSTEM/system_ext/priv-app/GoogleServicesFramework/GoogleServicesFramework.apk": b"synthetic apk",
        "SYSTEM/product/etc/permissions/privapp-permissions-google-product.xml": b"<permissions/>",
        "SYSTEM/system_ext/etc/permissions/privapp-permissions-google-system-ext.xml": b"<permissions/>",
    })
    specs = []
    for name, location, body in [
        ("nova.gguf", "SYSTEM/system_ext/etc/aios/models/nova.gguf", b"GGUFsynthetic model"),
        ("whisper.bin", "SYSTEM/system_ext/etc/aios/models/whisper.bin", b"lmggsynthetic model"),
        ("voice-data.zip", "SYSTEM/system_ext/etc/aios/models/voice-data.zip", b"synthetic voice data"),
        ("FDroid.apk", "SYSTEM/system_ext/app/SoiaFDroid/SoiaFDroid.apk", b"synthetic apk"),
        ("KDEConnect.apk", "SYSTEM/system_ext/app/SoiaKDEConnect/SoiaKDEConnect.apk", b"synthetic apk"),
    ]:
        data[location] = body
        specs.append({"name": name, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()})
    data["SYSTEM/system_ext/etc/aios/models/manifest.json"] = json.dumps({"version": 1, "artifacts": specs}).encode()
    return data


def write_archive(path, data):
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in data.items():
            archive.writestr(name, value)


def test_artifact_contents_do_not_certify_hardware(tmp_path):
    archive = tmp_path / "target-files.zip"
    write_archive(archive, contents())
    report = gsi.verify(archive)
    assert report["sdk"] == 36 and report["nova_in_system"]
    assert not report["compatibilita_hardware_verificata"]


@pytest.mark.parametrize("missing", list(contents()))
def test_missing_gsi_components_are_rejected(tmp_path, missing):
    data = contents()
    del data[missing]
    archive = tmp_path / "target-files.zip"
    write_archive(archive, data)
    with pytest.raises(gsi.InvalidGsi, match="incompleto"):
        gsi.verify(archive)


def test_wrong_sdk_or_permissions_are_rejected(tmp_path):
    archive = tmp_path / "target-files.zip"
    data = contents()
    data["SYSTEM/build.prop"] = b"ro.build.version.sdk=35\n"
    write_archive(archive, data)
    with pytest.raises(gsi.InvalidGsi, match="SDK"):
        gsi.verify(archive)
    data = contents()
    data["SYSTEM/system_ext/etc/permissions/privapp-permissions-org.aios.nova.xml"] = b"<permissions/>"
    write_archive(archive, data)
    with pytest.raises(gsi.InvalidGsi, match="Permessi"):
        gsi.verify(archive)


def test_corrupted_offline_content_is_rejected(tmp_path):
    data = contents()
    data["SYSTEM/system_ext/etc/aios/models/nova.gguf"] = b"tampered"
    archive = tmp_path / "target-files.zip"
    write_archive(archive, data)
    with pytest.raises(gsi.InvalidGsi, match="offline"):
        gsi.verify(archive)


def test_sign_script_selects_its_product_and_runs_content_validation(tmp_path):
    """Il firmatario simulato controlla la selezione; questo test non prova firme reali."""
    phone = tmp_path / "phone"
    scripts = phone / "scripts"
    scripts.mkdir(parents=True)
    original = path.parents[1]
    for name in ("comune.sh", "firma.sh", "verifica-gsi.py"):
        shutil.copyfile(original / "scripts" / name, scripts / name)
    target = json.loads((original / "dispositivi.json").read_text())["bersagli"]["gsi"]
    (phone / "dispositivi.json").write_text(json.dumps({"bersagli": {"gsi": target}}))
    sources = tmp_path / "sources"
    checkout = sources / f'aosp-{target["ramo"]}'
    releasetools = checkout / "build/tools/releasetools"
    releasetools.mkdir(parents=True)
    (releasetools / "sign_target_files_apks.py").write_text(
        "import shutil, sys\nshutil.copyfile(sys.argv[-2], sys.argv[-1])\n")
    out = checkout / "out/target/product/generic_arm64/obj/PACKAGING/target_files_intermediates"
    out.mkdir(parents=True)
    write_archive(out / "aios_gsi_arm64-target_files.zip", contents())
    other = out / "aios_shiba-target_files.zip"
    write_archive(other, {"IMAGES/system.img": b"wrong-product"})
    os.utime(other, (2_000_000_000, 2_000_000_000))
    env = {**os.environ, "AIOS_SORGENTI": str(sources), "AIOS_CHIAVI": str(tmp_path / "keys")}
    env.pop("AIOS_TARGET_FILES", None)
    result = subprocess.run(["bash", str(scripts / "firma.sh"), "gsi"], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert (phone / "uscita/gsi/system.img").read_bytes() == b"system"
    assert gsi.verify(phone / "uscita/gsi/target_files-firmati.zip")["sdk"] == 36


@pytest.mark.parametrize("complete", [True, False])
def test_build_checks_its_unsigned_output_and_limits_parallelism(tmp_path, complete):
    """Soong simulato: un comando riuscito non basta se manca Nova nel risultato."""
    phone = tmp_path / "phone"
    scripts = phone / "scripts"
    scripts.mkdir(parents=True)
    original = path.parents[1]
    for name in ("comune.sh", "compila.sh", "verifica-gsi.py", "esporta-prototipo.py"):
        shutil.copyfile(original / "scripts" / name, scripts / name)
    target = json.loads((original / "dispositivi.json").read_text())["bersagli"]["gsi"]
    (phone / "dispositivi.json").write_text(json.dumps({"bersagli": {"gsi": target}}))
    native = phone / "packages/apps/Nova/native/arm64-v8a"
    native.mkdir(parents=True)
    (native / "aios-llama-server").write_bytes(b"simulated native executable")
    sources = tmp_path / "sources"
    checkout = sources / f'aosp-{target["ramo"]}'
    (checkout / "build").mkdir(parents=True)
    (checkout / "build/envsetup.sh").write_text(
        ': "$ZSH_VERSION"  # variabile opzionale letta anche da envsetup AOSP\n'
        'lunch() { printf "%s\\n" "$@" > lunch-args; }\n'
        'm() { printf "%s\\n" "$@" > build-args; }\n')
    out = checkout / "out/target/product/generic_arm64/obj/PACKAGING/target_files_intermediates"
    out.mkdir(parents=True)
    data = contents()
    if not complete:
        del data["SYSTEM/system_ext/priv-app/Nova/Nova.apk"]
    write_archive(out / "aios_gsi_arm64-target_files.zip", data)
    env = {**os.environ, "AIOS_SORGENTI": str(sources), "AIOS_BUILD_JOBS": "4"}
    env.pop("ZSH_VERSION", None)
    result = subprocess.run(["bash", str(scripts / "compila.sh"), "gsi"], env=env,
                            capture_output=True, text=True, timeout=10)
    assert (checkout / "lunch-args").read_text().strip() == "aios_gsi_arm64-trunk_staging-user"
    assert (checkout / "build-args").read_text().splitlines() == ["-j4", "target-files-package"]
    assert (result.returncode == 0) == complete, result.stderr
    if complete:
        output = phone / "uscita/gsi/prototipo"
        assert (output / "system.img").read_bytes() == b"system"
        assert (output / "vbmeta.img").read_bytes() == b"vbmeta"
        report = json.loads((output / "prototipo.json").read_text())
        assert report["google_incluso"] and not report["production_release"]
    if not complete:
        assert "GSI rifiutata" in result.stderr and "Compilato." not in result.stdout
