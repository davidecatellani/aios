"""Vicini anche senza Wi-Fi: telefono e PC si trovano e si collegano da soli (come iPhone e Mac).

1. **Riconoscersi** — ogni dispositivo AIOS annuncia via Bluetooth a basso consumo (BLE) un
   codice di 8 byte che cambia ogni 15 minuti: un HMAC del tempo con un segreto che hanno solo
   i dispositivi dell'utente. Gli estranei vedono numeri casuali, sempre diversi: non possono
   riconoscere né seguire il telefono, né fingersi uno dei tuoi dispositivi.
2. **Collegarsi** — se non c'è una rete in comune, Nova sceglie il collegamento (choose_link):
   - *Wi-Fi diretto*: il PC crea una rete nascosta, senza internet, con nome e password ricavati
     dal segreto (cambiano ogni giorno, non serve scambiarli); il telefono la riconosce e ci entra
     senza staccarsi dai dati mobili. Veloce: per foto e file.
   - *Bluetooth*: una piccola rete via Bluetooth (PAN) aperta dal telefono. Lenta ma leggera, e non
     tocca il Wi-Fi del PC: per notifiche, SMS, tastiera, domande a Nova.
   - *Internet dal telefono* (hotspot istantaneo): il telefono condivide i dati con il PC. Solo
     se lo chiedi, o se l'hai permesso, e mai con la batteria del telefono bassa.
3. **Sciogliere** — quando il telefono si allontana o torna una rete in comune, il collegamento
   creato da Nova si chiude da solo.

Con il collegamento attivo KDE Connect e la pagina del telefono funzionano come a casa: sono
sulla stessa rete. Il telefono legge le richieste del PC (entra nella rete, apri il Bluetooth,
condividi internet) dal suo codice BLE, firmato con lo stesso segreto: solo il tuo PC può
chiederglielo.

Il segreto: per i telefoni abbinati con il QR deriva dalla loro chiave (il PC ne conserva solo
l'impronta, che basta); per i dispositivi con l'identità AIOS dalla chiave di sincronizzazione.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..crypto import hkdf
from ..tools.base import Runner

COMPANY = 0xFFFF  # dati del produttore BLE: 0xFFFF è riservato alle prove (da registrare un identificativo vero)
VERSION = 1
WINDOW = 900  # il codice cambia ogni 15 minuti
ROLES = {"pc": 0, "telefono": 1, "tablet": 2}
ROLE_NAMES = {v: k for k, v in ROLES.items()}
# bit del byte «flags» (protetti dall'HMAC: nessuno li può cambiare)
F_INTERNET = 1           # ho internet da condividere
F_RETE = 2               # ho aperto la rete diretta AIOS: entra
F_CHIEDE_INTERNET = 4    # condividi internet con me
F_CHIEDE_BT = 8          # apri la rete Bluetooth
F_BATTERIA_BASSA = 16
F_IN_CARICA = 32
CONNECTION = "aios-vicino"
CONNECTION_BT = "aios-vicino-bt"
ZONE = "aios-vicino"
LOST_AFTER = 3  # giri senza vedere il telefono prima di chiudere il collegamento


@dataclass
class Secret:
    name: str
    key: bytes
    bt: str = ""  # indirizzo Bluetooth classico del telefono (per la rete Bluetooth)


def secret_from_sync_key(sync_key: bytes, name: str = "i tuoi dispositivi AIOS") -> Secret:
    return Secret(name, hkdf(sync_key, b"aios-vicino-v1"))


def secret_from_device(key_hash_hex: str, name: str, bt: str = "") -> Secret:
    """Dal telefono abbinato con il QR: il telefono calcola lo stesso valore dalla sua chiave (sha256)."""
    return Secret(name, hkdf(bytes.fromhex(key_hash_hex), b"aios-vicino-v1"), bt)


def _mac(key: bytes, window: int, role: int, flags: int) -> bytes:
    msg = b"aios-beacon" + window.to_bytes(8, "big") + bytes([role, flags])
    return hmac.new(key, msg, hashlib.sha256).digest()[:8]


def make_beacon(secret: Secret, role: str, flags: int, now: float) -> bytes:
    r = ROLES[role]
    return bytes([VERSION, r, flags & 0xFF]) + _mac(secret.key, int(now // WINDOW), r, flags & 0xFF)


@dataclass
class Beacon:
    secret: Secret
    role: str
    flags: int
    address: str = ""
    rssi: int | None = None

    def has(self, flag: int) -> bool:
        return bool(self.flags & flag)


def read_beacon(data: bytes, secrets: list[Secret], now: float) -> Beacon | None:
    """Riconosce un codice di uno dei propri dispositivi (finestre di 15 minuti vicine, per gli orologi
    un po' sfasati). None per tutti gli altri."""
    if len(data) != 11 or data[0] != VERSION or data[1] not in ROLE_NAMES:
        return None
    window = int(now // WINDOW)
    for secret in secrets:
        for w in (window, window - 1, window + 1):
            if hmac.compare_digest(_mac(secret.key, w, data[1], data[2]), data[3:]):
                return Beacon(secret, ROLE_NAMES[data[1]], data[2])
    return None


def network_for(secret: Secret, now: float, day_offset: int = 0) -> tuple[str, str]:
    """Nome e password della rete diretta: ricavati dal segreto, cambiano ogni giorno (UTC)."""
    day = int(now // 86400) + day_offset
    d = hkdf(secret.key, b"aios-vicino-rete" + day.to_bytes(4, "big"), 24)
    return "AIOS-" + d[:3].hex(), base64.urlsafe_b64encode(d[3:18]).decode()


# --- decidere --------------------------------------------------------------------------------------------
@dataclass
class Situation:
    same_network: bool          # già raggiungibili (KDE Connect li vede)
    pc_wifi_free: bool          # il Wi-Fi del PC non è collegato a nessuna rete
    pc_internet: bool
    peer: Beacon
    need: str = "leggero"       # leggero | pesante | internet
    share_internet: str = "chiedi"  # chiedi | sempre | mai
    pc_low_battery: bool = False


@dataclass
class Choice:
    kind: str     # nessuno | rete | wifi | bluetooth | internet
    reason: str
    ask: bool = False  # serve il permesso dell'utente


def choose_link(s: Situation) -> Choice:
    if s.same_network:
        return Choice("rete", "Siete già sulla stessa rete: non serve altro.")
    low = s.peer.has(F_BATTERIA_BASSA) and not s.peer.has(F_IN_CARICA)
    if s.need == "internet" and not s.pc_internet:
        if not s.peer.has(F_INTERNET):
            return Choice("bluetooth", "Il telefono non ha internet da condividere: mi collego solo per i tuoi dati.")
        if low:
            return Choice("bluetooth", "Il telefono ha poca batteria: non gli faccio condividere internet.")
        if s.share_internet == "mai":
            return Choice("bluetooth", "Hai scelto di non usare internet del telefono.")
        return Choice("internet", "Uso internet del telefono (hotspot).", ask=s.share_internet != "sempre")
    if s.need == "pesante" and s.pc_wifi_free and not low:
        return Choice("wifi", "Apro una rete Wi-Fi diretta tra PC e telefono: veloce, senza internet e senza "
                              "consumare i dati del telefono.")
    why = ("il Wi-Fi del PC è occupato" if not s.pc_wifi_free else
           "il telefono ha poca batteria" if low else "per notifiche, SMS e tastiera basta")
    return Choice("bluetooth", f"Collegamento via Bluetooth: {why}.")


# --- Bluetooth a basso consumo (BlueZ) --------------------------------------------------------------------
def parse_managed_objects(text: str) -> list[tuple[str, bytes, int | None]]:
    """Da `busctl --json=short call org.bluez / …ObjectManager GetManagedObjects`: (indirizzo, dati, RSSI)."""
    try:
        doc = json.loads(text)
    except ValueError:
        return []
    data = doc.get("data", doc) if isinstance(doc, dict) else doc
    objects = data[0] if isinstance(data, list) and data else data
    found = []
    if not isinstance(objects, dict):
        return found
    for ifaces in objects.values():
        dev = ifaces.get("org.bluez.Device1") if isinstance(ifaces, dict) else None
        if not isinstance(dev, dict):
            continue
        address = _val(dev.get("Address")) or ""
        rssi = _val(dev.get("RSSI"))
        mdata = _val(dev.get("ManufacturerData")) or {}
        for company, value in (mdata.items() if isinstance(mdata, dict) else []):
            if str(company) == str(COMPANY):
                raw = _val(value)
                if isinstance(raw, list):
                    found.append((address, bytes(raw), rssi if isinstance(rssi, int) else None))
    return found


def _val(v: Any) -> Any:
    return v.get("data") if isinstance(v, dict) and "type" in v and "data" in v else v


class BleScanner:
    def __init__(self, runner: Runner | None = None):
        self.runner = runner or Runner()

    def available(self) -> bool:
        return self.runner.has("bluetoothctl") and self.runner.has("busctl")

    def scan(self, seconds: int = 5) -> list[tuple[str, bytes, int | None]]:
        self.runner.run(["bluetoothctl", "--timeout", str(seconds), "scan", "on"])
        code, out = self.runner.run(["busctl", "--json=short", "call", "org.bluez", "/",
                                     "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"])
        return parse_managed_objects(out) if code == 0 else []


class BleAdvertiser:
    """Annuncio BLE tramite bluetoothctl (resta attivo finché il processo vive)."""

    def __init__(self, popen: Callable[..., Any] = subprocess.Popen):
        self.popen, self.proc, self.payload = popen, None, b""

    @staticmethod
    def commands(payload: bytes) -> list[str]:
        data = " ".join(f"0x{b:02x}" for b in payload)
        return ["menu advertise", f"manufacturer 0x{COMPANY:04x} {data}", "back", "advertise on"]

    def set(self, payload: bytes) -> None:
        if payload == self.payload and self.proc is not None and self.proc.poll() is None:
            return
        self.stop()
        try:
            self.proc = self.popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, text=True)
            self.proc.stdin.write("\n".join(self.commands(payload)) + "\n")
            self.proc.stdin.flush()
            self.payload = payload
        except (OSError, AttributeError):
            self.proc, self.payload = None, b""

    def stop(self) -> None:
        if self.proc is not None:
            try:
                self.proc.stdin.write("advertise off\nquit\n")
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc, self.payload = None, b""


# --- reti (NetworkManager) -------------------------------------------------------------------------------
class Links:
    def __init__(self, runner: Runner | None = None):
        self.runner = runner or Runner()

    def available(self) -> bool:
        return self.runner.has("nmcli")

    def wifi(self) -> tuple[str, bool]:
        """(scheda Wi-Fi, libera?) — libera se non è collegata a nessuna rete (o solo alla nostra)."""
        code, out = self.runner.run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
        for line in out.splitlines() if code == 0 else []:
            parts = line.split(":")
            if len(parts) >= 4 and parts[1] == "wifi":
                busy = parts[2].startswith("connected") and parts[3] not in ("", CONNECTION)
                return parts[0], not busy
        return "", False

    def internet(self) -> bool:
        code, out = self.runner.run(["nmcli", "-t", "-f", "CONNECTIVITY", "general"])
        return code == 0 and out.strip() == "full"

    def host_wifi(self, device: str, ssid: str, password: str) -> bool:
        self.runner.run(["nmcli", "connection", "delete", CONNECTION])
        add = ["nmcli", "connection", "add", "type", "wifi", "ifname", device, "con-name", CONNECTION,
               "autoconnect", "no", "ssid", ssid, "802-11-wireless.mode", "ap", "802-11-wireless.hidden", "yes",
               "ipv4.method", "shared", "ipv6.method", "ignore",
               "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
        if self._zone_exists(ZONE):  # firewall: solo la pagina del telefono e KDE Connect (image/files/etc/firewalld)
            add += ["connection.zone", ZONE]
        return self.runner.run(add)[0] == 0 and self.runner.run(["nmcli", "connection", "up", CONNECTION])[0] == 0

    def _zone_exists(self, zone: str) -> bool:
        if not self.runner.has("firewall-cmd"):
            return False
        code, out = self.runner.run(["firewall-cmd", "--get-zones"])
        return code == 0 and zone in out.split()

    def join_wifi(self, device: str, ssid: str, password: str) -> bool:
        self.runner.run(["nmcli", "connection", "delete", CONNECTION])
        self.runner.run(["nmcli", "device", "wifi", "rescan", "ifname", device])
        return self.runner.run(["nmcli", "device", "wifi", "connect", ssid, "password", password, "ifname", device,
                                "name", CONNECTION, "hidden", "yes"])[0] == 0

    def join_bluetooth(self, mac: str) -> bool:
        if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac or ""):
            return False
        self.runner.run(["nmcli", "connection", "delete", CONNECTION_BT])
        add = ["nmcli", "connection", "add", "type", "bluetooth", "con-name", CONNECTION_BT, "autoconnect", "no",
               "bluetooth.bdaddr", mac.upper(), "bluetooth.type", "panu"]
        return self.runner.run(add)[0] == 0 and self.runner.run(["nmcli", "connection", "up", CONNECTION_BT])[0] == 0

    def down(self) -> None:
        for name in (CONNECTION, CONNECTION_BT):
            self.runner.run(["nmcli", "connection", "down", name])
            self.runner.run(["nmcli", "connection", "delete", name])


# --- il regista ------------------------------------------------------------------------------------------
LABELS = {"wifi": "Wi-Fi diretto", "bluetooth": "Bluetooth", "internet": "internet del telefono"}


@dataclass
class Nearby:
    scanner: Any
    advertiser: Any
    links: Any
    secrets: Callable[[], list[Secret]]
    role: str = "pc"
    clock: Callable[[], float] = time.time
    share_internet: str = "chiedi"
    link: str = ""            # collegamento creato da Nova: wifi | bluetooth | internet
    peer: Beacon | None = None
    missing: int = 0
    want: str = ""            # richiesta dell'utente: internet | wifi | bluetooth
    want_until: float = 0.0
    log: list[str] = field(default_factory=list)

    def request(self, kind: str, minutes: float = 60) -> None:
        """«usa internet del telefono», «collegati al telefono» (kind = auto per lasciar decidere a Nova)."""
        self.want = "" if kind == "auto" else kind
        self.want_until = self.clock() + minutes * 60

    def release(self) -> str:
        was = self.link
        self.want, self.link = "", ""
        self.links.down()
        self._advertise(0)
        return f"Ho chiuso il collegamento ({LABELS.get(was, was)})." if was else "Non c'era un collegamento da chiudere."

    def _advertise(self, flags: int) -> None:
        secrets = self.secrets()
        if not secrets:
            self.advertiser.stop()
            return
        # un solo codice per volta: il telefono abbinato (o l'identità) più recente
        self.advertiser.set(make_beacon(secrets[0] if self.peer is None else self.peer.secret, self.role, flags,
                                        self.clock()))

    def round(self, same_network: bool, need: str = "leggero", pc_low_battery: bool = False,
              confirm: Callable[[str], bool] | None = None) -> str:
        """Un giro: guarda chi c'è vicino e decide. → stato in breve (per i registri)."""
        now = self.clock()
        if self.want and now > self.want_until:
            self.want = ""
        secrets = self.secrets()
        mine = [b for b in (self._recognize(addr, data, rssi, secrets, now)
                            for addr, data, rssi in self.scanner.scan()) if b and b.role != self.role]
        internet_here = self.links.internet()
        base_flags = F_INTERNET if internet_here else 0
        if pc_low_battery:
            base_flags |= F_BATTERIA_BASSA
        if not mine:
            self.missing += 1
            if self.link and self.missing >= LOST_AFTER:
                was = self.link
                self.links.down()
                self.link, self.peer = "", None
                self._advertise(base_flags)
                return f"telefono lontano: chiuso {LABELS[was]}"
            self._advertise(base_flags)
            return "nessun dispositivo vicino" if not self.link else f"in attesa ({LABELS[self.link]})"
        self.missing = 0
        self.peer = max(mine, key=lambda b: b.rssi if b.rssi is not None else -999)
        device, wifi_free = self.links.wifi()
        if self.want in ("wifi", "bluetooth", "internet"):
            need = {"wifi": "pesante", "bluetooth": "leggero", "internet": "internet"}[self.want]
        choice = choose_link(Situation(same_network and self.link == "", wifi_free and bool(device), internet_here,
                                       self.peer, need, "sempre" if self.want == "internet" else self.share_internet,
                                       pc_low_battery))
        if self.want == "bluetooth" and choice.kind == "wifi":
            choice = Choice("bluetooth", "Mi hai chiesto il Bluetooth.")
        if choice.kind == "rete":
            if self.link:
                self.links.down()
                self.link = ""
            self._advertise(base_flags)
            return "stessa rete"
        if choice.kind == self.link:
            self._advertise(self._flags_for(choice.kind, base_flags))
            return f"collegato ({LABELS[self.link]})"
        if choice.ask and not (confirm and confirm(choice.reason)):
            choice = Choice("bluetooth", "Senza il tuo permesso non uso internet del telefono.")
            if self.link == "bluetooth":
                return "collegato (Bluetooth)"
        self._advertise(self._flags_for(choice.kind, base_flags))
        ok = self._open(choice.kind, device)
        if ok:
            if self.link and self.link != choice.kind:
                self.log.append(f"cambio: {LABELS[self.link]} → {LABELS[choice.kind]}")
            self.link = choice.kind
            self.log.append(choice.reason)
            return f"collegato ({LABELS[choice.kind]})"
        return f"provo {LABELS[choice.kind]}: il telefono non ha ancora risposto"

    @staticmethod
    def _flags_for(kind: str, base: int) -> int:
        return base | {"wifi": F_RETE, "bluetooth": F_CHIEDE_BT, "internet": F_CHIEDE_INTERNET}.get(kind, 0)

    def _recognize(self, address: str, data: bytes, rssi: int | None, secrets: list[Secret], now: float) -> Beacon | None:
        b = read_beacon(data, secrets, now)
        if b:
            b.address, b.rssi = address, rssi
        return b

    def _open(self, kind: str, device: str) -> bool:
        assert self.peer is not None
        now = self.clock()
        if kind == "wifi":
            if self.link == "wifi":
                return True
            self.links.down()
            return self.links.host_wifi(device, *network_for(self.peer.secret, now))
        if kind == "internet":  # il telefono apre la rete con lo stesso nome e la stessa password
            self.links.down()
            return any(self.links.join_wifi(device, *network_for(self.peer.secret, now, offset)) for offset in (0, -1))
        if kind == "bluetooth":
            if self.link == "wifi":
                self.links.down()
            return self.links.join_bluetooth(self.peer.secret.bt)
        return False

    def status(self) -> str:
        if self.link and self.peer:
            return (f"Collegato a {self.peer.secret.name} via {LABELS[self.link]}"
                    + (f": {self.log[-1]}" if self.log else "."))
        if self.peer and self.missing == 0:
            return f"{self.peer.secret.name} è qui vicino; siete sulla stessa rete o non serve un collegamento."
        return "Nessun tuo dispositivo vicino (lo cerco via Bluetooth, anche senza Wi-Fi)."
