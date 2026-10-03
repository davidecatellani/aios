"""Rispondere al telefono dal PC.

Il PC diventa il vivavoce Bluetooth del telefono (profilo HFP, ruolo «hands-free»):
l'audio della chiamata passa da microfono e altoparlanti del PC e la chiamata si
comanda con oFono (servizio di sistema, D-Bus). KDE Connect intanto mostra chi chiama.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..tools.base import Runner

WIREPLUMBER_CONF = "wireplumber/wireplumber.conf.d/51-aios-telefono.conf"
WIREPLUMBER_TEXT = """# Generato da AIOS: il PC fa da vivavoce Bluetooth del telefono
monitor.bluez.properties = {
  bluez5.roles = [ a2dp_sink a2dp_source hfp_hf hfp_ag hsp_hs hsp_ag ]
  bluez5.hfphsp-backend = "ofono"
}
"""


@dataclass
class Call:
    path: str
    number: str
    state: str  # incoming | active | dialing | alerting | held | waiting
    name: str = ""

    @property
    def who(self) -> str:
        return self.name or self.number or "numero sconosciuto"


def _value(v):
    return v.get("data") if isinstance(v, dict) and "data" in v else v


class Ofono:
    def __init__(self, runner: Runner | None = None):
        self.runner = runner or Runner()

    def _call(self, path: str, interface: str, method: str) -> dict | None:
        code, out = self.runner.run(["busctl", "--system", "--json=short", "call", "org.ofono", path, interface, method])
        if code != 0:
            return None
        try:
            return json.loads(out)
        except ValueError:
            return None

    def _objects(self, path: str, interface: str, method: str) -> list[tuple[str, dict]]:
        reply = self._call(path, interface, method)
        if not reply:
            return []
        data = reply.get("data") or [[]]
        return [(obj, {k: _value(v) for k, v in props.items()}) for obj, props in data[0]]

    def calls(self) -> list[Call]:
        found = []
        for modem, props in self._objects("/", "org.ofono.Manager", "GetModems"):
            if not props.get("Online", True):
                continue
            for path, c in self._objects(modem, "org.ofono.VoiceCallManager", "GetCalls"):
                found.append(Call(path, str(c.get("LineIdentification", "")), str(c.get("State", "")),
                                  str(c.get("Name", ""))))
        return found

    def incoming(self) -> Call | None:
        return next((c for c in self.calls() if c.state in ("incoming", "waiting")), None)

    def answer(self) -> str:
        call = self.incoming()
        if call is None:
            return "Non c'è nessuna chiamata in arrivo."
        if self._call(call.path, "org.ofono.VoiceCall", "Answer") is None:
            return "Non sono riuscito a rispondere: il telefono è collegato in Bluetooth al PC?"
        return f"Risposto a {call.who}: parla pure, l'audio passa dal PC."

    def hang_up(self) -> str:
        calls = [c for c in self.calls() if c.state in ("incoming", "waiting", "active", "dialing", "alerting")]
        if not calls:
            return "Non c'è nessuna chiamata."
        for c in calls:
            self._call(c.path, "org.ofono.VoiceCall", "Hangup")
        return "Chiamata rifiutata." if calls[0].state in ("incoming", "waiting") else "Chiamata chiusa."


def setup_hands_free(runner: Runner | None = None, config_home: Path | None = None) -> list[str]:
    """Prepara il PC a fare da vivavoce: configurazione audio (utente) e oFono (sistema, con password)."""
    runner = runner or Runner()
    base = config_home or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    conf = base / WIREPLUMBER_CONF
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(WIREPLUMBER_TEXT)
    errors = []
    for cmd in (["pkexec", "systemctl", "enable", "--now", "ofono.service"],
                ["systemctl", "--user", "restart", "wireplumber.service"]):
        code, out = runner.run(cmd)
        if code != 0:
            errors.append(f"{' '.join(cmd)}: {out[-200:]}")
    return errors
