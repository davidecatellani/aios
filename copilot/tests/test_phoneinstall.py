import base64
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from aios_copilot import modelcatalog
from aios_copilot import phoneinstall as pi
from aios_copilot.tools.base import Runner


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


PROPS = {
    "google": {"ro.product.manufacturer": "Google", "ro.product.model": "Pixel 8", "ro.product.device": "shiba"},
    "motorola": {"ro.product.manufacturer": "motorola", "ro.product.model": "moto g84 5G", "ro.product.device": "bangkk"},
    "xiaomi": {"ro.product.manufacturer": "Xiaomi", "ro.product.model": "Redmi Note 13", "ro.product.device": "sapphire"},
    "oppo": {"ro.product.manufacturer": "OPPO", "ro.product.model": "CPH2525", "ro.product.device": "OP5A0BL1"},
    "samsung": {"ro.product.manufacturer": "samsung", "ro.product.model": "SM-A546B", "ro.product.device": "a54x"},
}
MOTO_CODE = "ABCDEFGHIJ1234567890"


class FakePhone(Runner):
    """Un telefono che cambia stato come uno vero."""

    def __init__(self, brand="google", battery=80, us_model=False):
        super().__init__(which=lambda p: p if p in ("adb", "fastboot", "heimdall") else None)
        self.brand, self.battery, self.mode, self.unlocked, self.ran = brand, battery, "adb", False, []
        self.props = {**PROPS[brand], "ro.build.version.sdk": "34", "ro.product.cpu.abi": "arm64-v8a",
                      "ro.treble.enabled": "true", "sys.oem_unlock_allowed": "1", "ro.boot.flash.locked": "1"}
        if us_model:
            self.props["ro.product.model"] = "SM-S918U"

    def run(self, cmd):
        self.ran.append(cmd)
        c = [x for i, x in enumerate(cmd) if not (cmd[i - 1:i] == ["-s"] or x == "-s")]
        if c[:2] == ["adb", "devices"]:
            return 0, "List of devices attached\n" + ("R5CW1234 device product:x model:y\n" if self.mode == "adb" else "")
        if c[:3] == ["adb", "shell", "getprop"]:
            return 0, "".join(f"[{k}]: [{v}]\n" for k, v in self.props.items())
        if c[:4] == ["adb", "shell", "dumpsys", "battery"]:
            return 0, f"Current Battery Service state:\n  level: {self.battery}\n"
        if c[:3] == ["adb", "reboot", "bootloader"]:
            self.mode = "fastboot"
        if c[:2] == ["fastboot", "devices"]:
            return 0, "R5CW1234\tfastboot\n" if self.mode == "fastboot" else ""
        if c[:3] == ["fastboot", "getvar", "all"]:
            return 0, f"(bootloader) unlocked: {'yes' if self.unlocked else 'no'}\n(bootloader) product: {self.props['ro.product.device']}\n"
        if c[:3] == ["fastboot", "flashing", "unlock"]:
            self.unlocked = True
        if c[:3] == ["fastboot", "flashing", "lock"]:
            self.unlocked = False
        if c[:3] == ["fastboot", "oem", "get_unlock_data"]:
            return 0, "(bootloader) 3A25450432070806#\n(bootloader) 5A593232334C3456\n(bootloader) 4D6F746F72000000\nOKAY\n"
        if c[:3] == ["fastboot", "oem", "unlock"]:
            if c[3] != MOTO_CODE:
                return 1, "FAILED (remote: 'invalid unlock code')"
            self.unlocked = True
        if c[:2] == ["fastboot", "reboot"] and len(c) == 2:
            self.mode = "adb"
        if c[:2] == ["heimdall", "detect"]:
            return (0, "Device detected") if self.mode == "download" else (1, "")
        return 0, ""


def build(kind="gsi", codename="", brand=""):
    files = [{"nome": "system.img", "url": "https://x/system.img", "sha256": "0" * 64, "partizione": "system"},
             {"nome": "vbmeta.img", "url": "https://x/vbmeta.img", "sha256": "1" * 64, "partizione": "vbmeta"}]
    if kind == "dispositivo":
        files = [{"nome": f"aios-{codename}.zip", "url": "https://x/a.zip", "sha256": "2" * 64}]
    if kind == "recovery":
        files = [{"nome": "vbmeta.img", "url": "https://x/v", "sha256": "3" * 64, "partizione": "vbmeta"},
                 {"nome": "recovery.img", "url": "https://x/r", "sha256": "4" * 64, "partizione": "recovery"}]
    return pi.Build(f"AIOS {kind} {codename}", "1.0", kind, brand, codename, files=files,
                    avb_key={"nome": "avb_pkmd.bin", "url": "https://x/k", "sha256": "5" * 64} if kind == "dispositivo" else {})


CATALOG = [build("dispositivo", "shiba", "google"), build("gsi"), build("recovery", "a54x", "samsung")]


def run_install(phone, answers=None, catalog=CATALOG):
    info = pi.detect(phone)
    system, recovery = pi.choose_build(info, catalog)
    steps = pi.plan_for(info, system, recovery, {})
    asked = []

    def ask(step):
        asked.append(step.id)
        return (answers or {}).get(step.id, "ok")

    ok = pi.Installer(phone, info, steps, ask, backup=lambda p: "backup fatto", wait=lambda s, t: _reach(phone, s),
                      timeout=1).run()
    return ok, asked, steps


def _reach(phone, state):
    if state == "fastbootd":
        return phone.mode == "fastboot"
    if state == "sbloccato":
        return phone.unlocked
    if state == "bloccato":
        return not phone.unlocked
    if state == "adb" and phone.mode != "adb":
        phone.mode = "adb"  # l'utente ha fatto ciò che chiedeva il passo
        if phone.brand == "xiaomi":
            phone.unlocked = True  # sbloccato con Mi Unlock, lo strumento di Xiaomi
    return True


def flashed(phone):
    return [c for c in phone.ran if c[0] in ("fastboot", "heimdall") and c[1:2] != ["devices"] and "getvar" not in c
            and "detect" not in c]


def test_detects_every_brand():
    for brand in PROPS:
        info = pi.detect(FakePhone(brand))
        assert info.brand == brand and info.mode == "adb" and info.battery == 80 and info.treble
    assert pi.detect(FakePhone("xiaomi")).label == "Xiaomi Redmi Note 13"


def test_preflight_stops_before_touching_the_phone():
    assert "Batteria al 30%" in " ".join(pi.preflight(pi.detect(FakePhone(battery=30)), CATALOG))
    assert "nordamericano" in " ".join(pi.preflight(pi.detect(FakePhone("samsung", us_model=True)), CATALOG))
    pixel7 = FakePhone()
    pixel7.props["ro.product.device"] = "panther"
    assert "non c'è ancora un'immagine" in " ".join(pi.preflight(pi.detect(pixel7), CATALOG))  # niente GSI sui Pixel
    assert pi.preflight(pi.detect(FakePhone("motorola")), CATALOG) == []
    assert "Non vedo nessun telefono" in pi.preflight(pi.PhoneInfo("nessuno"), CATALOG)[0]


def test_pixel_install_unlocks_flashes_and_relocks():
    phone = FakePhone("google")
    ok, asked, steps = run_install(phone)
    assert ok and asked == ["conferma", "collega"]  # telefono già pronto: conferma, poi il collegamento finale
    cmds = [" ".join(c[3:] if c[1] == "-s" else c[1:]) for c in flashed(phone)]
    assert cmds == ["flashing unlock", "erase avb_custom_key", "flash avb_custom_key avb_pkmd.bin",
                    "-w update --skip-reboot aios-shiba.zip", "reboot-bootloader", "flashing lock", "reboot"]
    assert not phone.unlocked  # richiuso: avvio verificato con la chiave di AIOS


def test_refusing_the_wipe_changes_nothing():
    phone = FakePhone("google")
    ok, _, steps = run_install(phone, {"conferma": "no"})
    assert not ok and flashed(phone) == [] and phone.mode == "adb"
    assert next(s for s in steps if s.id == "conferma").detail.startswith("Interrotto da te: il telefono non è stato modificato")


def test_motorola_unlock_code():
    phone = FakePhone("motorola")
    ok, asked, steps = run_install(phone, {"codice": MOTO_CODE.lower()})
    assert ok and "codice" in asked
    assert next(s for s in steps if s.id == "dati_sblocco").detail == "3A25450432070806#5A593232334C34564D6F746F72000000"  # in una riga, come vuole Motorola
    cmds = [" ".join(c[3:] if c[1] == "-s" else c[1:]) for c in flashed(phone)]
    assert "--disable-verity --disable-verification flash vbmeta vbmeta.img" in cmds and "flash system system.img" in cmds
    bad = FakePhone("motorola")
    ok, _, steps = run_install(bad, {"codice": "sbagliato!"})
    assert not ok and "non sembra valido" in next(s for s in steps if s.status == "errore").detail


def test_xiaomi_and_oppo_need_the_brand_permission():
    phone = FakePhone("xiaomi")
    ok, asked, _ = run_install(phone)
    assert ok and asked[:1] == ["mi_unlock"] and "sblocco" in asked
    assert ["fastboot", "-s", "R5CW1234", "flash", "system", "system.img"] in phone.ran
    oppo = FakePhone("oppo")
    ok, asked, _ = run_install(oppo, {"deep_testing": "annulla"})
    assert not ok and flashed(oppo) == []  # senza Deep Testing ci si ferma prima di toccare qualcosa


def test_samsung_warns_about_knox_and_uses_heimdall():
    phone = FakePhone("samsung")
    ok, asked, _ = run_install(phone)
    assert asked.index("knox") < asked.index("conferma")
    heimdall = next(c for c in phone.ran if c[0] == "heimdall" and c[1] == "flash")
    assert heimdall == ["heimdall", "flash", "--VBMETA", "vbmeta.img", "--RECOVERY", "recovery.img", "--no-reboot"]
    knox_no = FakePhone("samsung")
    ok, _, _ = run_install(knox_no, {"knox": "no"})
    assert not ok and flashed(knox_no) == []


def test_dry_run_executes_nothing(tmp_path):
    phone = FakePhone("google")
    info = pi.detect(phone)
    steps = pi.plan_for(info, CATALOG[0], None, {})
    before = len(phone.ran)
    assert pi.Installer(phone, info, steps, lambda s: "ok", dry_run=True).run()
    assert len(phone.ran) == before
    log = sorted(pi.log_dir().glob("*.log"))[-1].read_text()
    assert "flashing unlock" in log and "avb_custom_key" in log  # nel registro si vede cosa farebbe


def test_downloads_are_verified(tmp_path):
    data = b"immagine di aios"
    good = {"nome": "system.img", "url": "https://x/system.img", "sha256": hashlib.sha256(data).hexdigest()}

    def fetch(urls, target, seconds):
        (target / "system.img").write_bytes(data)
        return True, len(data), len(data)

    assert pi.download_build(pi.Build("x", "1", "gsi", files=[good]), tmp_path, fetch)["system.img"].read_bytes() == data
    with pytest.raises(pi.InstallError, match="impronta"):
        pi.download_build(pi.Build("x", "1", "gsi", files=[{**good, "sha256": "0" * 64}]), tmp_path, fetch)
    assert not (tmp_path / "system.img").exists()


def test_signed_catalog_and_local_image(tmp_path):
    pem = tmp_path / "k.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(pem)], check=True, capture_output=True)
    pub = base64.b64decode(modelcatalog.public_key(pem))
    doc = tmp_path / "c.json"
    doc.write_text(json.dumps({"immagini": [{"nome": "AIOS GSI", "versione": "0.1", "tipo": "gsi",
                                             "file": [{"nome": "system.img", "url": "https://x/s", "sha256": "a" * 64}]}]}))
    sig = modelcatalog.sign_file(doc, pem).read_bytes()
    served = {"https://c/t.json": doc.read_bytes(), "https://c/t.json.sig": sig}
    assert pi.update_catalog("https://c/t.json", served.__getitem__, keys=[pub]) == 1
    assert pi.load_catalog()[0].name == "AIOS GSI"
    with pytest.raises(pi.InstallError):
        pi.update_catalog("https://c/t.json", served.__getitem__, keys=[bytes(32)])
    img = tmp_path / "system.img"
    img.write_bytes(b"gsi di google")
    local = pi.local_build(img)
    assert local.files[0]["sha256"] == hashlib.sha256(b"gsi di google").hexdigest()
