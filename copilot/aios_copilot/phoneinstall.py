"""Installare AIOS (base AOSP) su un telefono collegato via USB, guidati da Nova.

Marche supportate e strada per ciascuna:

- **Google Pixel**: immagine AIOS per il modello (codename). Sblocco con `fastboot
  flashing unlock`, installazione, chiave di avvio di AIOS (`avb_custom_key`) e
  **richiusura** del bootloader: l'avvio resta verificato, come con il sistema di Google.
- **Motorola**: sblocco con il codice che Motorola invia per email (`fastboot oem
  get_unlock_data` → sito Motorola → `fastboot oem unlock CODICE`), poi AIOS in
  versione **GSI** (immagine generica Treble) da fastbootd. Il bootloader resta aperto.
- **Xiaomi / Redmi / POCO**: sblocco ufficiale di Xiaomi (account Mi associato da
  «Stato di Mi Unlock», su HyperOS richiesta dall'app Xiaomi Community, poi attesa
  imposta da Xiaomi, di solito 3-7 giorni, e Mi Unlock); poi GSI da fastbootd.
- **Oppo**: solo i modelli per cui Oppo offre l'app ufficiale «Deep Testing»; dopo
  l'approvazione `fastboot flashing unlock` e GSI. Senza l'app non si può sbloccare.
- **Samsung**: niente fastboot: «modalità download» e heimdall. Sblocco dal telefono
  (fa scattare per sempre il contatore Knox: Samsung Wallet, Cartella sicura… non
  funzioneranno più), poi recovery di AIOS con fastbootd e sistema GSI. I modelli
  nordamericani (Snapdragon, sigla che finisce per U/U1/W) non si possono sbloccare.

Sicurezza: immagini solo dal catalogo firmato (Ed25519, come i modelli AI) con
impronta SHA-256 verificata; batteria ≥ 50%; backup prima di tutto; nessuna
cancellazione senza una conferma esplicita; ogni comando nel registro; modalità prova.

    aios-installa-telefono [stato | prova | installa | backup | ripristina --cartella …]
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .privacy import private_dir
from .tools.base import Runner

MIN_BATTERY = 50
SAMSUNG_LOCKED_SUFFIX = re.compile(r"^SM-[A-Z]\d{3,4}(?:U1?|W)$")  # Nord America: bootloader non sbloccabile
MOTOROLA_UNLOCK_SITE = "https://en-us.support.motorola.com/app/standalone/bootloader/unlock-your-device-a"
BRANDS = {"google": "Google Pixel", "samsung": "Samsung", "motorola": "Motorola", "xiaomi": "Xiaomi", "oppo": "Oppo"}
XIAOMI_UNLOCK_INFO = "https://en.miui.com/unlock/"


class InstallError(RuntimeError):
    pass


# --- riconoscere il telefono ----------------------------------------------------------------------


@dataclass
class PhoneInfo:
    mode: str  # adb | non-autorizzato | fastboot | download | nessuno
    serial: str = ""
    brand: str = ""  # google | samsung | motorola | altro
    model: str = ""
    codename: str = ""
    sdk: int = 0
    abi: str = ""
    treble: bool = False
    oem_unlock_allowed: bool | None = None
    unlocked: bool | None = None
    battery: int | None = None

    @property
    def label(self) -> str:
        return f"{BRANDS.get(self.brand, self.brand.capitalize() or 'Telefono')} {self.model}".strip()


def _props(text: str) -> dict[str, str]:
    return dict(re.findall(r"^\[([^\]]+)\]:\s*\[([^\]]*)\]", text, re.M))


def _brand(manufacturer: str) -> str:
    m = manufacturer.lower()
    if "google" in m:
        return "google"
    if "samsung" in m:
        return "samsung"
    if "motorola" in m or "lenovo" in m:
        return "motorola"
    if m in ("xiaomi", "redmi", "poco"):
        return "xiaomi"
    if "oppo" in m:
        return "oppo"
    return m


def detect(runner: Runner) -> PhoneInfo:
    if runner.has("adb"):
        code, out = runner.run(["adb", "devices", "-l"])
        for line in out.splitlines()[1:] if code == 0 else []:
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            if state == "unauthorized":
                return PhoneInfo("non-autorizzato", serial)
            if state in ("device", "recovery", "sideload"):
                return _adb_info(runner, serial)
    if runner.has("fastboot"):
        code, out = runner.run(["fastboot", "devices"])
        serial = out.split()[0] if code == 0 and out.strip() else ""
        if serial:
            return _fastboot_info(runner, serial)
    if runner.has("heimdall"):
        code, _ = runner.run(["heimdall", "detect"])
        if code == 0:
            return PhoneInfo("download", brand="samsung")
    return PhoneInfo("nessuno")


def _adb_info(runner: Runner, serial: str) -> PhoneInfo:
    _, out = runner.run(["adb", "-s", serial, "shell", "getprop"])
    p = _props(out)
    _, bat = runner.run(["adb", "-s", serial, "shell", "dumpsys", "battery"])
    level = re.search(r"^\s*level:\s*(\d+)", bat, re.M)
    allowed = p.get("sys.oem_unlock_allowed")
    flash_locked = p.get("ro.boot.flash.locked")
    return PhoneInfo("adb", serial, _brand(p.get("ro.product.manufacturer", "")), p.get("ro.product.model", ""),
                     p.get("ro.product.device", ""), int(p.get("ro.build.version.sdk", "0") or 0),
                     p.get("ro.product.cpu.abi", ""), p.get("ro.treble.enabled") == "true",
                     None if allowed is None else allowed == "1", None if flash_locked is None else flash_locked == "0",
                     int(level.group(1)) if level else None)


def _fastboot_info(runner: Runner, serial: str) -> PhoneInfo:
    _, out = runner.run(["fastboot", "-s", serial, "getvar", "all"])
    v = dict(re.findall(r"^\(bootloader\)\s*([\w.-]+):\s*(.*)$", out, re.M))
    unlocked = v.get("unlocked")
    product = v.get("product", "")
    brand = "google" if v.get("variant", "").startswith(("MSM", "SM")) or product in PIXEL_CODENAMES else \
        "motorola" if "moto" in out.lower() or v.get("ro.carrier") else ""
    return PhoneInfo("fastboot", serial, brand, product, product, unlocked=None if unlocked is None else unlocked == "yes")


PIXEL_CODENAMES = {"oriole", "raven", "bluejay", "panther", "cheetah", "lynx", "tangorpro", "felix", "shiba", "husky",
                   "akita", "tokay", "caiman", "komodo", "comet", "tegu", "frankel", "blazer", "mustang"}


# --- catalogo firmato delle immagini ----------------------------------------------------------------


@dataclass
class Build:
    name: str
    version: str
    kind: str  # dispositivo | gsi | recovery
    brand: str = ""
    codename: str = ""
    abi: str = "arm64-v8a"
    min_sdk: int = 0
    files: list[dict[str, str]] = field(default_factory=list)  # {nome, url, sha256, partizione}
    avb_key: dict[str, str] = field(default_factory=dict)


def catalog_path() -> Path:
    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios") / "telefoni.json"


def parse_catalog(data: bytes) -> list[Build]:
    doc = json.loads(data)
    builds = []
    for raw in doc.get("immagini", []):
        files = raw.get("file", [])
        if not files or not all(f.get("url", "").startswith("https://") and re.fullmatch(r"[0-9a-f]{64}", f.get("sha256", ""))
                                for f in files):
            raise InstallError(f"{raw.get('nome')}: ogni file va scaricato in https e con impronta SHA-256")
        builds.append(Build(raw["nome"], str(raw.get("versione", "")), raw.get("tipo", "gsi"), raw.get("marca", ""),
                            raw.get("codename", ""), raw.get("abi", "arm64-v8a"), int(raw.get("min_sdk", 0)), files,
                            raw.get("chiave_avb", {})))
    return builds


def update_catalog(url: str, fetch: Callable[[str], bytes], keys: list[bytes] | None = None) -> int:
    """Scarica il catalogo delle immagini: accettato solo se firmato dal progetto AIOS."""
    from .modelcatalog import trusted_keys, verify

    keys = trusted_keys() if keys is None else keys
    data = fetch(url)
    if not keys or not verify(data, base64.b64decode(fetch(url + ".sig")), keys):
        raise InstallError("firma del catalogo delle immagini non valida")
    builds = parse_catalog(data)
    catalog_path().write_bytes(data)
    return len(builds)


def load_catalog() -> list[Build]:
    try:
        return parse_catalog(catalog_path().read_bytes())
    except (OSError, ValueError, InstallError):
        return []


def choose_build(phone: PhoneInfo, builds: list[Build]) -> tuple[Build | None, Build | None]:
    """→ (sistema, recovery). Prima l'immagine fatta per quel modello, poi la GSI se il telefono è compatibile."""
    exact = next((b for b in builds if b.kind == "dispositivo" and b.codename == phone.codename), None)
    recovery = next((b for b in builds if b.kind == "recovery" and b.codename == phone.codename), None)
    if exact:
        return exact, recovery
    if phone.brand == "google":
        return None, None  # sui Pixel solo immagini dedicate (con chiave di avvio e richiusura)
    gsi = next((b for b in builds if b.kind == "gsi" and phone.treble and b.abi == phone.abi and phone.sdk >= b.min_sdk), None)
    return gsi, recovery


# --- piano dei passi ---------------------------------------------------------------------------------


@dataclass
class Step:
    id: str
    title: str
    kind: str  # utente | auto | conferma | codice | attesa
    text: str = ""
    commands: list[list[str]] = field(default_factory=list)
    wait_for: str = ""  # adb | fastboot | fastbootd | download | sbloccato
    status: str = "da fare"  # da fare | in corso | fatto | errore | saltato
    detail: str = ""


DEV_OPTIONS = ("Sul telefono: Impostazioni › Info telefono › tocca 7 volte «Numero build» (si attivano le Opzioni "
               "sviluppatore). Poi Impostazioni › Sistema › Opzioni sviluppatore: attiva «Sblocco OEM» e «Debug USB». "
               "Quando il telefono chiede se fidarsi di questo computer, tocca «Consenti».")


def common_start(phone: PhoneInfo) -> list[Step]:
    steps = []
    if phone.mode in ("nessuno", "non-autorizzato") or phone.oem_unlock_allowed is False:
        steps.append(Step("sviluppatore", "Prepara il telefono", "utente", DEV_OPTIONS, wait_for="adb"))
    steps += [
        Step("batteria", "Controllo della batteria", "auto", f"Serve almeno il {MIN_BATTERY}% di carica."),
        Step("whatsapp", "Backup di WhatsApp", "utente",
             "Se usi WhatsApp: apri WhatsApp › Impostazioni › Chat › Backup delle chat › «Esegui backup». Su AIOS non c'è "
             "Google Drive: il backup lo copio io sul PC e lo rimetto sul telefono nuovo. Se non usi WhatsApp, continua."),
        Step("backup", "Backup completo sul PC", "auto",
             "Copio sul PC foto e video, documenti, scaricati, musica, rubrica, WhatsApp e, se Android lo permette, "
             "SMS e calendario."),
        Step("immagini", "Scarico e verifico AIOS", "auto", "Solo immagini firmate dal progetto AIOS, con impronta verificata."),
        Step("conferma", "Conferma: il telefono verrà cancellato", "conferma",
             "Lo sblocco cancella tutto ciò che c'è sul telefono. Il backup è fatto. Vuoi installare AIOS?"),
    ]
    return steps


def plan_for(phone: PhoneInfo, system: Build, recovery: Build | None, files: dict[str, Path]) -> list[Step]:
    """I passi per quel telefono. `files` = nome file → percorso scaricato e verificato."""
    steps = common_start(phone)
    f = lambda name: str(files.get(name, name))  # noqa: E731
    if phone.brand == "google":
        image = f(system.files[0]["nome"])
        steps += [
            Step("bootloader", "Riavvio nel bootloader", "auto", commands=[["adb", "reboot", "bootloader"]], wait_for="fastboot"),
            Step("sblocco", "Sblocco del bootloader", "attesa",
                 "Sul telefono: con i tasti del volume scegli «Unlock the bootloader» e conferma con il tasto di accensione.",
                 commands=[["fastboot", "flashing", "unlock"]], wait_for="sbloccato"),
            Step("chiave", "Chiave di avvio di AIOS", "auto",
                 commands=[["fastboot", "erase", "avb_custom_key"], ["fastboot", "flash", "avb_custom_key", f(system.avb_key.get("nome", "avb_pkmd.bin"))]]),
            Step("installa", "Installo AIOS", "auto", "Ci vogliono alcuni minuti: non scollegare il cavo.",
                 commands=[["fastboot", "-w", "update", "--skip-reboot", image]]),
            Step("richiudi", "Richiudo il bootloader", "attesa",
                 "Sul telefono: con i tasti del volume scegli «Lock the bootloader» e conferma con il tasto di accensione. "
                 "Da qui in poi l'avvio è verificato con la chiave di AIOS.",
                 commands=[["fastboot", "reboot-bootloader"], ["fastboot", "flashing", "lock"]], wait_for="bloccato"),
        ]
    elif phone.brand == "motorola":
        steps += [
            Step("bootloader", "Riavvio nel bootloader", "auto", commands=[["adb", "reboot", "bootloader"]], wait_for="fastboot"),
            Step("dati_sblocco", "Dati per lo sblocco", "auto", commands=[["fastboot", "oem", "get_unlock_data"]]),
            Step("codice", "Codice di sblocco di Motorola", "codice",
                 f"Apri {MOTOROLA_UNLOCK_SITE}, accedi, incolla la stringa qui sotto e chiedi il codice: arriva per email. "
                 "Poi scrivilo qui."),
            Step("sblocco", "Sblocco del bootloader", "attesa", "Conferma sul telefono se te lo chiede.",
                 commands=[["fastboot", "oem", "unlock", "{codice}"], ["fastboot", "oem", "unlock", "{codice}"]], wait_for="sbloccato"),
            *gsi_steps(system, f),
        ]
    elif phone.brand == "xiaomi":
        steps = [Step("mi_unlock", "Permesso di sblocco di Xiaomi", "utente",
                      "Xiaomi chiede un permesso, una volta sola: Impostazioni › Opzioni sviluppatore › «Stato di Mi Unlock» › "
                      "associa il tuo account Mi (con i dati mobili attivi). Sui telefoni con HyperOS serve anche la richiesta "
                      "dall'app Xiaomi Community («Sblocca il bootloader»). Xiaomi fa poi aspettare qualche giorno (di solito "
                      f"3-7): finché non è passato, l'installazione si ferma qui. Dettagli: {XIAOMI_UNLOCK_INFO}"), *steps]
        steps += [
            Step("bootloader", "Riavvio nel bootloader", "auto", commands=[["adb", "reboot", "bootloader"]], wait_for="fastboot"),
            Step("sblocco", "Sblocco con Mi Unlock", "utente",
                 "Avvia Mi Unlock (lo strumento ufficiale di Xiaomi) con il telefono collegato in fastboot e premi "
                 "«Unlock». Il telefono si cancella e si riavvia: quando torna, riattiva «Debug USB».",
                 wait_for="adb"),
            Step("bootloader2", "Riavvio nel bootloader", "auto", commands=[["adb", "reboot", "bootloader"]], wait_for="sbloccato"),
            *gsi_steps(system, f),
        ]
    elif phone.brand == "oppo":
        steps = [Step("deep_testing", "Permesso di sblocco di Oppo", "utente",
                      "Oppo permette lo sblocco solo con la sua app ufficiale «Deep Testing», disponibile per alcuni modelli: "
                      "cercala per il tuo modello sul sito o nella community di Oppo, installala e chiedi l'approvazione. Se "
                      "per il tuo modello non esiste, purtroppo il telefono non si può sbloccare: scegli «annulla». "
                      "Continua quando l'app dice che la richiesta è approvata."), *steps]
        steps += [
            Step("avvia_deep", "Avvio del test approfondito", "utente",
                 "Nell'app Deep Testing tocca «Avvia test approfondito»: il telefono si riavvia in fastboot.", wait_for="fastboot"),
            Step("sblocco", "Sblocco del bootloader", "attesa",
                 "Sul telefono conferma lo sblocco con i tasti del volume e il tasto di accensione.",
                 commands=[["fastboot", "flashing", "unlock"]], wait_for="sbloccato"),
            *gsi_steps(system, f),
        ]
    elif phone.brand == "samsung":
        if recovery is None:
            raise InstallError("per questo Samsung manca la recovery di AIOS nel catalogo")
        steps.insert(len(steps) - 1, Step("knox", "Attenzione: Knox", "conferma",
                                          "Lo sblocco di un Samsung fa scattare per sempre il contatore Knox: Samsung Wallet, "
                                          "Cartella sicura e alcune funzioni di Samsung Health non funzioneranno più, "
                                          "nemmeno tornando al sistema originale. Vuoi continuare?"))
        steps += [
            Step("download", "Modalità download", "utente",
                 "Spegni il telefono. Tieni premuti Volume su + Volume giù e collega il cavo. Quando compare l'avviso, "
                 "tieni premuto Volume su per sbloccare il bootloader: il telefono si cancella e si riavvia. Poi rifai i "
                 "passi delle Opzioni sviluppatore (attiva «Debug USB»), spegni e rientra in modalità download "
                 "(Volume su + Volume giù + cavo, poi Volume su una volta).", wait_for="download"),
            Step("recovery", "Installo la recovery di AIOS", "auto",
                 commands=[["heimdall", "flash", "--VBMETA", f(by_partition(recovery, "vbmeta")),
                            "--RECOVERY", f(by_partition(recovery, "recovery")), "--no-reboot"]]),
            Step("avvia_recovery", "Avvio nella recovery", "utente",
                 "Tieni premuti Volume giù + Accensione finché lo schermo si spegne, poi subito Volume su + Accensione "
                 "finché compare la recovery di AIOS. Scegli «Avanzate» › «Entra in fastboot».", wait_for="fastboot"),
            *gsi_steps(system, f, from_recovery=True),
        ]
    else:
        raise InstallError(f"{phone.label}: per ora AIOS si installa su Pixel, Samsung, Motorola, Xiaomi/Redmi e Oppo")
    steps += [
        Step("avvio", "Primo avvio di AIOS", "auto", "Il primo avvio può richiedere qualche minuto.",
             commands=[["fastboot", "reboot"]]),
        Step("debug_nuovo", "Collega il telefono nuovo", "utente",
             "Sul telefono, nella prima schermata di AIOS, scegli «Ripristina dal computer» (oppure attiva «Debug USB» "
             "dalle Opzioni sviluppatore) e tocca «Consenti» quando chiede se fidarsi di questo computer.", wait_for="adb"),
        Step("ripristino", "Ripristino di foto, file e WhatsApp", "auto",
             "Rimetto sul telefono ciò che ho salvato. Il backup resta comunque sul PC."),
        Step("app", "Rubrica e WhatsApp", "utente",
             "Rubrica: apri Contatti › Importa › «contatti.vcf» (in Download). WhatsApp: installalo e, quando lo chiede, "
             "tocca «Ripristina»: trova da solo il backup che ho rimesso al suo posto."),
        Step("collega", "Collego il telefono alla tua identità", "utente",
             "Sul telefono, nella prima schermata di AIOS, scegli «Ho già AIOS sul computer» e inquadra il codice che ti "
             "mostro qui (oppure attiva «Debug USB» e lo faccio io via cavo)."),
    ]
    return steps


def by_partition(build: Build, partition: str) -> str:
    for item in build.files:
        if item.get("partizione") == partition:
            return item["nome"]
    raise InstallError(f"{build.name}: manca il file per la partizione «{partition}»")


def gsi_steps(system: Build, f: Callable[[str], str], from_recovery: bool = False) -> list[Step]:
    """GSI (immagine generica Treble): vbmeta senza verifica, poi system da fastbootd."""
    image = next((x["nome"] for x in system.files if x.get("partizione", "system") == "system"), system.files[0]["nome"])
    steps = []
    if not from_recovery:  # dalla recovery di AIOS si è già in fastbootd e vbmeta è già a posto
        vbmeta = next((x["nome"] for x in system.files if x.get("partizione") == "vbmeta"), "")
        cmds = [["fastboot", "--disable-verity", "--disable-verification", "flash", "vbmeta", f(vbmeta)]] if vbmeta else []
        cmds.append(["fastboot", "reboot", "fastboot"])  # fastbootd: le partizioni dinamiche si scrivono da qui
        steps.append(Step("prepara_gsi", "Preparo il telefono", "auto", commands=cmds, wait_for="fastbootd"))
    steps.append(Step("installa", "Installo AIOS", "auto", "Ci vogliono alcuni minuti: non scollegare il cavo.",
                      commands=[["fastboot", "erase", "system"], ["fastboot", "flash", "system", f(image)], ["fastboot", "-w"]]))
    return steps


def preflight(phone: PhoneInfo, builds: list[Build]) -> list[str]:
    """Motivi per cui non si può procedere (vuoto = si può)."""
    problems = []
    if phone.mode == "nessuno":
        problems.append("Non vedo nessun telefono: collegalo con il cavo USB (un cavo dati, non solo di ricarica).")
    if phone.mode == "non-autorizzato":
        problems.append("Il telefono chiede se fidarsi di questo computer: tocca «Consenti» sullo schermo del telefono.")
    if phone.brand and phone.brand not in BRANDS:
        problems.append(f"{phone.label}: per ora AIOS si installa su Google Pixel, Samsung, Motorola, Xiaomi/Redmi e Oppo.")
    if phone.brand == "samsung" and SAMSUNG_LOCKED_SUFFIX.match(phone.model.upper()):
        problems.append(f"{phone.model} è un modello nordamericano: Samsung non permette di sbloccarlo.")
    if phone.battery is not None and phone.battery < MIN_BATTERY:
        problems.append(f"Batteria al {phone.battery}%: caricala almeno al {MIN_BATTERY}% prima di iniziare.")
    if phone.mode == "adb" and phone.brand in BRANDS:
        system, _ = choose_build(phone, builds)
        if system is None:
            problems.append(f"Nel catalogo non c'è ancora un'immagine di AIOS per {phone.label} ({phone.codename}).")
    return problems


# --- esecuzione --------------------------------------------------------------------------------------


def log_dir() -> Path:
    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "installazioni")


class Installer:
    """Esegue i passi; le domande all'utente passano da `ask` (pagina, terminale o test)."""

    def __init__(self, runner: Runner, phone: PhoneInfo, steps: list[Step], ask: Callable[[Step], str],
                 emit: Callable[[str, dict[str, Any]], None] = lambda k, d: None, dry_run: bool = False,
                 backup: Callable[[PhoneInfo], str] | None = None, wait: Callable[[str, float], bool] | None = None,
                 timeout: float = 600):
        self.runner, self.phone, self.steps, self.ask, self.emit = runner, phone, steps, ask, emit
        self.dry_run, self.timeout = dry_run, timeout
        self.backup = backup or (lambda p: backup_phone(runner, p))
        self.restore = lambda p, folder: restore_phone(runner, p, folder)
        self.backup_dir: Path | None = None
        self.wait = wait or self._wait
        self.values: dict[str, str] = {}
        self.log = log_dir() / f"{datetime.now():%Y%m%d-%H%M%S}.log"

    def _record(self, line: str) -> None:
        with self.log.open("a") as f:
            f.write(f"{datetime.now():%H:%M:%S} {line}\n")

    def _cmd(self, cmd: list[str]) -> str:
        cmd = [part.format(**self.values) if "{" in part else part for part in cmd]
        if cmd[0] in ("adb", "fastboot") and self.phone.serial and "-s" not in cmd:
            cmd = [cmd[0], "-s", self.phone.serial, *cmd[1:]]
        self._record("$ " + " ".join(cmd))
        if self.dry_run:
            return ""
        code, out = self.runner.run(cmd)
        self._record(out[-2000:])
        if code != 0:
            raise InstallError(f"«{' '.join(cmd[:4])}…» non è riuscito: {out.strip()[-300:]}")
        return out

    def _wait(self, state: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            p = detect(self.runner)
            if state == "adb" and p.mode == "adb" or state in ("fastboot", "fastbootd") and p.mode == "fastboot" \
                    or state == "download" and p.mode == "download" or state == "sbloccato" and p.unlocked \
                    or state == "bloccato" and p.mode == "fastboot" and p.unlocked is False:
                if p.serial:
                    self.phone.serial = p.serial
                return True
            time.sleep(2)
        return False

    def run(self) -> bool:
        for step in self.steps:
            step.status = "in corso"
            self.emit("passo", asdict(step))
            try:
                self._do(step)
            except InstallError as exc:
                step.status, step.detail = "errore", str(exc)
                self._record(f"ERRORE {step.id}: {exc}")
                self.emit("passo", asdict(step))
                return False
            if step.status == "in corso":
                step.status = "fatto"
            self.emit("passo", asdict(step))
            if step.status == "annullato":
                return False
        return True

    def _do(self, step: Step) -> None:
        if step.kind in ("utente", "conferma", "codice"):
            answer = self.ask(step)
            if answer in ("", "annulla", "no"):
                step.status = "annullato"
                step.detail = "Interrotto da te: il telefono non è stato modificato." if step.id in ("conferma", "knox") else "Interrotto da te."
                return
            if step.kind == "codice":
                code = re.sub(r"\s+", "", answer)
                if not re.fullmatch(r"[A-Z0-9]{10,40}", code.upper()):
                    raise InstallError("il codice di sblocco non sembra valido (lettere e numeri, senza spazi)")
                self.values[step.id] = code.upper()
        if step.id == "batteria" and not self.dry_run:
            level = detect(self.runner).battery
            if level is not None and level < MIN_BATTERY:
                raise InstallError(f"batteria al {level}%: serve almeno il {MIN_BATTERY}%")
        if step.id == "backup" and not self.dry_run:
            result = self.backup(self.phone)
            step.detail, self.backup_dir = result if isinstance(result, tuple) else (result, None)
        if step.id == "ripristino" and not self.dry_run:
            step.detail = self.restore(self.phone, self.backup_dir) if self.backup_dir else "Nessun backup da ripristinare."
        outputs = [self._cmd(cmd) for cmd in step.commands]
        if step.id == "dati_sblocco":
            data = "".join(re.findall(r"\(bootloader\)\s*([0-9A-Za-z#$]+)\s*$", "\n".join(outputs), re.M))
            step.detail = data or "(in modalità prova la stringa appare qui)"
        if step.wait_for and not self.dry_run and not self.wait(step.wait_for, self.timeout):
            raise InstallError("non vedo il telefono nello stato giusto: ricontrolla le istruzioni e riprova"
                               if step.kind == "utente" else f"il telefono non è arrivato allo stato «{step.wait_for}» in tempo")


BACKUP_FOLDERS = ("DCIM", "Pictures", "Movies", "Documents", "Download", "Music", "Recordings", "Notifications",
                  "Android/media/com.whatsapp")  # l'ultima: il backup locale di WhatsApp (su AOSP non c'è Google Drive)


def contacts_vcf(rows: str) -> str:
    """Le righe di «content query» della rubrica → un file .vcf importabile da qualsiasi app Contatti."""
    people: dict[str, dict[str, Any]] = {}
    for line in rows.splitlines():
        if not line.startswith("Row:"):
            continue
        fields = dict(re.findall(r"(\w+)=(.*?)(?=, \w+=|$)", line.split(" ", 2)[-1]))
        pid = fields.get("raw_contact_id") or fields.get("contact_id") or fields.get("display_name", "")
        person = people.setdefault(pid, {"name": fields.get("display_name", ""), "tel": [], "email": []})
        kind, value = fields.get("mimetype", ""), fields.get("data1", "")
        if value and value != "NULL":
            if kind.endswith("/phone_v2"):
                person["tel"].append(value)
            elif kind.endswith("/email_v2"):
                person["email"].append(value)
    cards = []
    for p in people.values():
        if not p["name"] or p["name"] == "NULL":
            continue
        esc = lambda t: t.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")  # noqa: E731
        lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{esc(p['name'])}", f"N:{esc(p['name'])};;;;"]
        lines += [f"TEL;TYPE=CELL:{t}" for t in dict.fromkeys(p["tel"])]
        lines += [f"EMAIL:{e}" for e in dict.fromkeys(p["email"])]
        cards.append("\r\n".join(lines + ["END:VCARD"]))
    return "\r\n".join(cards) + ("\r\n" if cards else "")


def backup_phone(runner: Runner, phone: PhoneInfo, dest: Path | None = None) -> tuple[str, Path]:
    """Copia sul PC ciò che conta del telefono (sola lettura). → (riepilogo, cartella del backup)."""
    from .xdg import resolve_folder

    dest = dest or resolve_folder("DOCUMENTS") / f"Backup telefono {phone.model or 'Android'} {datetime.now():%Y-%m-%d %H%M}"
    (dest / "file").mkdir(parents=True, exist_ok=True)
    adb = ["adb", "-s", phone.serial] if phone.serial else ["adb"]
    copied, manifest = [], {"telefono": asdict(phone), "quando": datetime.now().isoformat(), "cartelle": []}
    for folder in BACKUP_FOLDERS:
        target = dest / "file" / Path(folder).parent
        target.mkdir(parents=True, exist_ok=True)
        code, _ = runner.run([*adb, "pull", "-a", f"/sdcard/{folder}", str(target)])
        if code == 0:
            copied.append("WhatsApp" if "whatsapp" in folder else folder)
            manifest["cartelle"].append(folder)
    code, rows = runner.run([*adb, "shell", "content", "query", "--uri", "content://com.android.contacts/data",
                             "--projection", "raw_contact_id:display_name:mimetype:data1"])
    vcf = contacts_vcf(rows) if code == 0 else ""
    if vcf:
        (dest / "contatti.vcf").write_text(vcf)
        copied.append(f"rubrica ({vcf.count('BEGIN:VCARD')} contatti)")
    for name, uri, projection in (("sms.txt", "content://sms", "address:date:body:type"),
                                  ("calendario.txt", "content://com.android.calendar/events", "title:dtstart:dtend:eventLocation")):
        code, out = runner.run([*adb, "shell", "content", "query", "--uri", uri, "--projection", projection])
        if code == 0 and out.startswith("Row:"):
            (dest / name).write_text(out)
            copied.append(name.split(".")[0])
    (dest / "backup.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    os.chmod(dest, 0o700)
    return f"Backup in «{dest.name}»: {', '.join(copied) or 'niente da copiare'}.", dest


def restore_phone(runner: Runner, phone: PhoneInfo, backup: Path) -> str:
    """Rimette sul telefono nuovo i file del backup e prepara rubrica e WhatsApp."""
    adb = ["adb", "-s", phone.serial] if phone.serial else ["adb"]
    try:
        manifest = json.loads((backup / "backup.json").read_text())
    except (OSError, ValueError):
        raise InstallError(f"in {backup} non trovo un backup di AIOS")
    restored = []
    for folder in manifest.get("cartelle", []):
        source = backup / "file" / folder
        if not source.exists():
            continue
        parent = Path(folder).parent
        remote = "/sdcard/" if parent == Path(".") else f"/sdcard/{parent.as_posix()}/"
        runner.run([*adb, "shell", "mkdir", "-p", remote.rstrip("/")])
        code, out = runner.run([*adb, "push", str(source), remote])
        if code != 0:
            raise InstallError(f"ripristino di {folder} non riuscito: {out.strip()[-200:]}")
        restored.append("WhatsApp" if "whatsapp" in folder else folder)
    if (backup / "contatti.vcf").exists():
        runner.run([*adb, "push", str(backup / "contatti.vcf"), "/sdcard/Download/contatti.vcf"])
        restored.append("rubrica (da importare)")
    # le foto ripristinate compaiono subito in Galleria
    runner.run([*adb, "shell", "content", "call", "--uri", "content://media", "--method", "scan_volume", "--arg", "external_primary"])
    return f"Ripristinati: {', '.join(restored) or 'niente'}."


def download_build(build: Build, target: Path, fetch: Callable[..., Any] | None = None) -> dict[str, Path]:
    """Scarica (riprendendo) e verifica ogni file; un'impronta sbagliata ferma tutto."""
    from .models import file_download_step

    target.mkdir(parents=True, exist_ok=True)
    out = {}
    for item in [*build.files, *([build.avb_key] if build.avb_key else [])]:
        path = target / item["nome"]
        done = False
        while not done:
            done, _, _ = (fetch or file_download_step)([item["url"]], target, 30)
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            path.unlink(missing_ok=True)
            raise InstallError(f"{item['nome']}: impronta non corrispondente, file scartato")
        out[item["nome"]] = path
    return out


def local_build(image: Path, vbmeta: Path | None = None) -> Build:
    """Un'immagine GSI scaricata dall'utente (es. la GSI AOSP ufficiale di Google per provare):
    l'impronta si calcola qui e si mostra, da confrontare con quella pubblicata."""
    files = []
    for path, part in ((image, "system"), (vbmeta, "vbmeta")):
        if path is None:
            continue
        if not path.is_file():
            raise InstallError(f"non trovo {path}")
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        files.append({"nome": path.name, "url": path.as_uri(), "sha256": digest.hexdigest(), "partizione": part})
    return Build(f"immagine locale {image.name}", "locale", "gsi", files=files)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="aios-installa-telefono", description="Installa AIOS su un telefono collegato via USB")
    parser.add_argument("azione", nargs="?", default="stato", choices=["stato", "prova", "installa", "backup", "ripristina"])
    parser.add_argument("--immagine", type=Path, help="immagine GSI scaricata da te (system.img)")
    parser.add_argument("--vbmeta", type=Path, help="vbmeta.img da usare con la GSI")
    parser.add_argument("--cartella", type=Path, help="cartella del backup da ripristinare")
    args = parser.parse_args(argv)
    runner = Runner()
    phone = detect(runner)
    if args.azione in ("backup", "ripristina"):
        if phone.mode != "adb":
            print("Collega il telefono con «Debug USB» attivo e tocca «Consenti» sul telefono.")
            return 1
        if args.azione == "backup":
            print(backup_phone(runner, phone)[0])
        else:
            if not args.cartella:
                print("Indica la cartella del backup: --cartella \"Documenti/Backup telefono …\"")
                return 1
            print(restore_phone(runner, phone, args.cartella))
        return 0
    builds = load_catalog()
    local = local_build(args.immagine, args.vbmeta) if args.immagine else None
    print(f"Telefono: {phone.label if phone.mode != 'nessuno' else 'nessuno'} ({phone.mode})")
    problems = [p for p in preflight(phone, builds) if not (local and "Nel catalogo non c'è" in p)]
    if args.azione == "stato" or problems and args.azione == "installa":
        print("\n".join(problems) or "Si può installare AIOS.")
        return 1 if problems else 0
    if local:
        for item in local.files:
            print(f"Impronta di {item['nome']}: {item['sha256']} — confrontala con quella pubblicata da chi l'ha rilasciata.")
    system, recovery = choose_build(phone, builds)
    system = local or system or Build("AIOS (prova)", "0", "gsi", files=[{"nome": "aios-system.img", "url": "", "sha256": ""}])
    if local:
        files = {item["nome"]: Path(item["url"][7:]) for item in local.files}
    else:
        files = {} if args.azione == "prova" else download_build(system, log_dir() / "immagini")
    recovery = recovery or Build("recovery (prova)", "0", "recovery", files=[{"nome": "vbmeta.img", "partizione": "vbmeta"},
                                                                           {"nome": "recovery.img", "partizione": "recovery"}])
    steps = plan_for(phone, system, recovery, files)

    def ask(step: Step) -> str:
        print(f"\n▶ {step.title}\n  {step.text}" + (f"\n  {step.detail}" if step.detail else ""))
        if args.azione == "prova":
            return "PROVA1234567890" if step.kind == "codice" else "ok"
        prompt = "  Scrivi il codice: " if step.kind == "codice" else "  Scrivi «ok» per continuare (o «annulla»): "
        return input(prompt).strip()

    ok = Installer(runner, phone, steps, ask, lambda k, d: print(f"  [{d['status']}] {d['title']} {d.get('detail', '')}"),
                   dry_run=args.azione == "prova").run()
    print("\nFatto: AIOS è installato." if ok and args.azione != "prova" else "\nModalità prova completata." if ok else "\nInterrotto.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
