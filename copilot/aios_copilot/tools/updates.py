"""Aggiornamenti del sistema dal copilota."""

from __future__ import annotations

import re

from ..fastpath import Intent, normalize
from ..updates import Updates, set_auto
from .base import Runner, Tool, params


def make_tools(runner: Runner | None = None) -> list[Tool]:
    runner = runner or Runner()
    updates = Updates(runner)

    def update_status() -> str:
        return updates.describe()

    def update_now() -> str:
        found = updates.check()
        if not found:
            return "È tutto aggiornato. 👍"
        return "\n".join(updates.prepare(found))

    def rollback_system() -> str:
        return updates.rollback()

    def auto_updates(on: str = "sì") -> str:
        enabled = on.lower().strip() in ("sì", "si", "on", "attiva", "true", "yes")
        set_auto(enabled)
        return ("Aggiornamenti automatici attivi: scarico a riposo e in carica, e ti chiedo io quando riavviare."
                if enabled else "Aggiornamenti automatici disattivati: dimmi «aggiorna il sistema» quando vuoi.")

    def restart_to_update() -> str:
        code, out = runner.run(["systemctl", "reboot"])
        return "Riavvio per applicare l'aggiornamento…" if code == 0 else f"Riavvio non riuscito: {out[-200:]}"

    return [
        Tool("update_status", "Mostra se il sistema è aggiornato e se c'è un aggiornamento pronto.", params(), update_status),
        Tool("update_now", "Controlla e prepara subito gli aggiornamenti di sistema e app.", params(), update_now,
             requires_confirmation=True),
        Tool("rollback_system", "Torna alla versione precedente del sistema (al prossimo riavvio).", params(),
             rollback_system, requires_confirmation=True),
        Tool("auto_updates", "Attiva o disattiva gli aggiornamenti automatici.", params(on=("Attivi?", ["sì", "no"])),
             auto_updates),
        Tool("restart_to_update", "Riavvia il computer per applicare l'aggiornamento pronto.", params(),
             restart_to_update, requires_confirmation=True),
    ]


RE_STATUS = re.compile(r"^(?:ci sono|ho)\s+(?:degli\s+|nuovi\s+)?aggiornamenti\??$|^(?:il\s+)?(?:sistema|computer|pc)\s+è\s+aggiornato\??$"
                       r"|^stato\s+degli\s+aggiornamenti$")
RE_NOW = re.compile(r"^aggiorna\s+(?:il\s+sistema|il\s+computer|il\s+pc|tutto|aios)(?:\s+(?:ora|adesso|subito))?$")
RE_ROLLBACK = re.compile(r"^(?:torna|ritorna)\s+alla\s+versione\s+precedente(?:\s+del\s+sistema)?$")
RE_AUTO = re.compile(r"^(?P<v>attiva|disattiva)\s+(?:gli\s+)?aggiornamenti\s+automatici$")
RE_RESTART = re.compile(r"^riavvia\s+per\s+aggiornare$|^applica\s+l'aggiornamento$")


class UpdatesRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text)
        if RE_STATUS.match(low):
            return Intent("update_status", {})
        if RE_NOW.match(low):
            return Intent("update_now", {})
        if RE_ROLLBACK.match(low):
            return Intent("rollback_system", {})
        m = RE_AUTO.match(low)
        if m:
            return Intent("auto_updates", {"on": "sì" if m.group("v") == "attiva" else "no"})
        if RE_RESTART.match(low):
            return Intent("restart_to_update", {})
        return None
