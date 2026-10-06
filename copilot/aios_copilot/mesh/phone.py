"""Il telefono tramite KDE Connect (app per Android e iPhone, protocollo cifrato in rete locale).

Il demone kdeconnectd fa già scoperta e cifratura; SoIA lo comanda con kdeconnect-cli.
L'abbinamento si conferma una volta sola sul telefono; dopo, il collegamento è automatico
ogni volta che i due dispositivi sono sulla stessa rete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..tools.base import Runner

CLI = "kdeconnect-cli"
LINE = re.compile(r"^-\s+(?P<name>.+?):\s+(?P<id>[\w-]+)\s+\((?P<state>[^)]*)\)\s*$")


@dataclass
class Phone:
    id: str
    name: str
    paired: bool
    reachable: bool


class KdeConnect:
    def __init__(self, runner: Runner | None = None):
        self.runner = runner or Runner()

    def available(self) -> bool:
        return self.runner.has(CLI)

    def devices(self) -> list[Phone]:
        if not self.available():
            return []
        self.runner.run([CLI, "--refresh"])
        code, out = self.runner.run([CLI, "-l"])
        found = []
        for line in out.splitlines():
            m = LINE.match(line.strip())
            if m:
                state = m.group("state").lower()
                found.append(Phone(m.group("id"), m.group("name"), "paired" in state and "unpaired" not in state,
                                   "reachable" in state and "unreachable" not in state))
        return found

    def nearby(self) -> list[Phone]:
        return [p for p in self.devices() if p.paired and p.reachable]

    def find(self, name: str = "") -> Phone | None:
        phones = self.nearby()
        if name:
            phones = [p for p in phones if name.lower() in p.name.lower()] or phones
        return phones[0] if phones else None

    def _do(self, phone: Phone, *args: str) -> tuple[bool, str]:
        code, out = self.runner.run([CLI, "-d", phone.id, *args])
        return code == 0, out

    def pair(self, phone: Phone) -> bool:
        return self._do(phone, "--pair")[0]

    def unpair(self, phone: Phone) -> bool:
        return self._do(phone, "--unpair")[0]

    def ring(self, phone: Phone) -> bool:
        return self._do(phone, "--ring")[0]

    def share(self, phone: Phone, path: Path) -> bool:
        return self._do(phone, "--share", str(path))[0]

    def send_text(self, phone: Phone, text: str) -> bool:
        return self._do(phone, "--share-text", text)[0]

    def send_sms(self, phone: Phone, number: str, text: str) -> bool:
        return self._do(phone, "--send-sms", text, "--destination", number)[0]
