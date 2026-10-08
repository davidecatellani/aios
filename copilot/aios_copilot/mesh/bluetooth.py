"""Dispositivi Bluetooth condivisi tra i dispositivi dell'utente.

Le chiavi di abbinamento Bluetooth sono legate a ogni coppia di apparecchi: non si
possono copiare dal telefono al PC. SoIA ottiene lo stesso effetto così:

1. ogni dispositivo SoIA annota i dispositivi Bluetooth abbinati (nome, indirizzo, tipo)
   e l'elenco viaggia con la sincronizzazione cifrata (sync.py);
2. quando uno di quei dispositivi è vicino e raggiungibile, gli altri si abbinano da soli:
   cuffie e altoparlanti in automatico; tastiere, mouse e simili solo con conferma
   (chi si finge la tua tastiera potrebbe digitare al posto tuo).

Serve SoIA su entrambi i lati (Android da solo non condivide l'elenco). Molti dispositivi
vanno messi in modalità abbinamento, o supportano più collegamenti (multipoint).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from ..privacy import private_dir
from ..tools.base import Runner

MAC = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")
AUDIO_ICONS = ("audio-headset", "audio-headphones", "audio-card", "audio-speakers")
INPUT_ICONS = ("input-keyboard", "input-mouse", "input-gaming", "input-tablet")


def known_path() -> Path:
    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios") / "bluetooth.json"


def load_known() -> dict[str, dict[str, str]]:
    try:
        return json.loads(known_path().read_text())
    except (OSError, ValueError):
        return {}


def save_known(known: dict[str, dict[str, str]]) -> None:
    known_path().write_text(json.dumps(known, ensure_ascii=False, indent=1))


class Bluetooth:
    def __init__(self, runner: Runner | None = None):
        self.runner = runner or Runner()

    def available(self) -> bool:
        return self.runner.has("bluetoothctl")

    def _devices(self, *args: str) -> dict[str, str]:
        code, out = self.runner.run(["bluetoothctl", "devices", *args])
        found = {}
        for line in out.splitlines() if code == 0 else []:
            m = re.match(r"^Device\s+((?:[0-9A-F]{2}:){5}[0-9A-F]{2})\s+(.*)$", line.strip())
            if m:
                found[m.group(1)] = m.group(2).strip()
        return found

    def paired(self) -> dict[str, str]:
        return self._devices("Paired")

    def icon(self, mac: str) -> str:
        code, out = self.runner.run(["bluetoothctl", "info", mac])
        m = re.search(r"^\s*Icon:\s*(\S+)", out, re.M) if code == 0 else None
        return m.group(1) if m else ""

    def nearby(self, seconds: int = 8) -> dict[str, str]:
        self.runner.run(["bluetoothctl", "--timeout", str(seconds), "scan", "on"])
        return self._devices()

    def pair(self, mac: str) -> bool:
        for cmd in (["bluetoothctl", "pair", mac], ["bluetoothctl", "trust", mac], ["bluetoothctl", "connect", mac]):
            code, _ = self.runner.run(cmd)
            if code != 0 and cmd[1] != "connect":
                return False
        return True

    def remove(self, mac: str) -> bool:
        return self.runner.run(["bluetoothctl", "remove", mac])[0] == 0

    def remember_local(self) -> dict[str, dict[str, str]]:
        """Aggiunge all'elenco condiviso i dispositivi abbinati qui (mai togliere da soli)."""
        known = load_known()
        for mac, name in self.paired().items():
            if mac not in known:
                known[mac] = {"nome": name, "icona": self.icon(mac), "da": os.uname().nodename, "quando": str(int(time.time()))}
        save_known(known)
        return known


def share_round(bt: Bluetooth, notify: Callable[[str, str], None], ask: Callable[[str, str, dict[str, str]], str]) -> list[str]:
    """Un giro: annota i propri, cerca quelli dell'utente abbinati altrove e li abbina anche qui."""
    if not bt.available():
        return []
    known = bt.remember_local()
    mine = set(bt.paired())
    missing = {mac: info for mac, info in known.items() if mac not in mine}
    if not missing:
        return []
    done = []
    around = bt.nearby()
    for mac, info in missing.items():
        if mac not in around:
            continue
        icon = info.get("icona", "") or bt.icon(mac)
        name = info.get("nome", mac)
        if icon.startswith(INPUT_ICONS) or not icon.startswith(AUDIO_ICONS):
            # tastiere, mouse e dispositivi sconosciuti: solo se l'utente conferma
            if ask(f"🔵 {name} è qui", f"È già abbinato a un tuo altro dispositivo ({info.get('da', '')}). Lo collego anche qui?",
                   {"collega": "Collega"}) != "collega":
                continue
        if bt.pair(mac):
            done.append(name)
            notify(f"🎧 {name} collegato anche qui", "Era già abbinato a un tuo altro dispositivo.")
    return done


class BluetoothAdapter:
    """L'elenco dei dispositivi Bluetooth dell'utente nella sincronizzazione cifrata."""

    prefix = "bluetooth"

    def records(self) -> dict[str, Any]:
        return {mac: {"nome": i.get("nome", ""), "icona": i.get("icona", ""), "da": i.get("da", "")}
                for mac, i in load_known().items()}

    def apply(self, key: str, value: Any) -> None:
        if not MAC.match(key):
            return
        known = load_known()
        if value is None:
            known.pop(key, None)  # dimenticato su un altro dispositivo: non lo si cerca più
        elif isinstance(value, dict):
            known[key] = {"nome": str(value.get("nome", ""))[:80], "icona": str(value.get("icona", ""))[:40],
                          "da": str(value.get("da", ""))[:60]}
        save_known(known)


def forget(bt: Bluetooth, name: str) -> list[str]:
    """«Dimentica le cuffie»: via dall'elenco di tutti i dispositivi (e scollegato qui)."""
    known = load_known()
    hits = [mac for mac, i in known.items() if name.lower() in i.get("nome", "").lower()]
    for mac in hits:
        bt.remove(mac)
        known.pop(mac)
    save_known(known)
    return hits
