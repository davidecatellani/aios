"""Aggiornamenti del sistema dal copilota."""

from __future__ import annotations

import re
from typing import Callable

from ..fastpath import Intent, normalize
from ..updates import GITHUB_HELP, Updates, connect_github, load_state, set_auto
from .base import Runner, Tool, params


def ask_secret(runner: Runner, title: str, text: str) -> str | None:
    """Una finestra locale per incollare un token: il segreto non passa mai dal modello AI."""
    if not runner.has("zenity"):
        return None
    import subprocess

    try:
        proc = subprocess.run(["zenity", "--password", f"--title={title}", f"--text={text}"],
                              capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else ""


def make_tools(runner: Runner | None = None, updates: Updates | None = None,
               secret: Callable[[str, str], str | None] | None = None,
               connect: Callable[[str], str] = connect_github) -> list[Tool]:
    runner = runner or Runner()
    updates = updates or Updates(runner)
    secret = secret or (lambda title, text: ask_secret(runner, title, text))

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

    def connect_github_updates() -> str:
        from ..imageupdate import configured_repo

        repo = load_state().get("repo") or configured_repo() or "il repository di AIOS"
        help_text = GITHUB_HELP.format(repo=repo)
        token = secret("Aggiornamenti da GitHub", "Incolla il token di sola lettura (Contents: read) per " + repo)
        if token is None:
            return help_text.replace("nella finestra che apro", "nel Terminale con «aios-aggiornamenti github»")
        if not token:
            return help_text + "\n\nQuando hai il token, dimmi di nuovo «collega GitHub per gli aggiornamenti»."
        return connect(token)

    def update_from_usb() -> str:
        found = updates.check(("chiavetta",))
        system = next((u for u in found if u.kind == "sistema" and updates.package is not None), None)
        if system is None:
            return ("Non trovo una versione più recente di AIOS sulla chiavetta: copia nella chiavetta i file "
                    "«aios-aggiornamento…» della Release (tutti, senza riunirli) e inseriscila.")
        return "\n".join(updates.prepare([system]))

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
        Tool("connect_github_updates", "Collega il PC al repository GitHub privato di AIOS per ricevere le nuove "
             "versioni (permesso di sola lettura dell'utente, chiesto in una finestra locale).", params(),
             connect_github_updates),
        Tool("update_from_usb", "Prepara la nuova versione di AIOS dalla chiavetta inserita (si applica al riavvio, "
             "senza formattare).", params(), update_from_usb, requires_confirmation=True),
        Tool("restart_to_update", "Riavvia il computer per applicare l'aggiornamento pronto.", params(),
             restart_to_update, requires_confirmation=True),
    ]


RE_STATUS = re.compile(r"^(?:ci sono|ho)\s+(?:degli\s+|nuovi\s+)?aggiornamenti\??$|^(?:il\s+)?(?:sistema|computer|pc)\s+è\s+aggiornato\??$"
                       r"|^stato\s+degli\s+aggiornamenti$")
RE_NOW = re.compile(r"^aggiorna\s+(?:il\s+sistema|il\s+computer|il\s+pc|tutto|aios)(?:\s+(?:ora|adesso|subito))?$")
RE_ROLLBACK = re.compile(r"^(?:torna|ritorna)\s+alla\s+versione\s+precedente(?:\s+del\s+sistema)?$")
RE_AUTO = re.compile(r"^(?P<v>attiva|disattiva)\s+(?:gli\s+)?aggiornamenti\s+automatici$")
RE_GITHUB = re.compile(r"^(?:collega|configura|attiva)\s+github(?:\s+per\s+gli\s+aggiornamenti)?$"
                       r"|^aggiornamenti\s+da\s+github$")
RE_USB = re.compile(r"^aggiorna(?:\s+(?:il\s+sistema|aios))?\s+(?:dalla|con\s+la)\s+chiavetta$"
                    r"|^installa\s+l'aggiornamento\s+dalla\s+chiavetta$")
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
        if RE_GITHUB.match(low):
            return Intent("connect_github_updates", {})
        if RE_USB.match(low):
            return Intent("update_from_usb", {})
        return None
