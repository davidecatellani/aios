"""Aggiornamenti del sistema dal copilota."""

from __future__ import annotations

import re
from typing import Callable

from ..fastpath import Intent, normalize
from ..updates import GITHUB_HELP, Updates, connect_github, load_state, public_repo, set_auto
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

    def switch_variant(variante: str) -> str:
        return updates.switch_variant(variante)

    def update_now() -> str:
        if updates.busy():
            return updates.progress_text()
        found = updates.check()
        if not found:
            return "È tutto aggiornato."
        big = [u for u in found if u.kind == "sistema"]
        if not big:
            return "\n".join(updates.prepare(found))
        # il sistema nuovo pesa gigabyte: si scarica in sottofondo, Nova resta libera e avvisa alla fine
        updates.start_background(found)
        return (f"Ho cominciato: {big[0].summary}. Lo scarico in sottofondo, puoi continuare a usare il computer. "
                "Chiedimi «come va l'aggiornamento?» per sapere a che punto è; ti avviso io quando è pronto.")

    def rollback_system() -> str:
        return updates.rollback()

    def auto_updates(on: str = "sì") -> str:
        enabled = on.lower().strip() in ("sì", "si", "on", "attiva", "true", "yes")
        set_auto(enabled)
        return ("Aggiornamenti automatici attivi: scarico a riposo e in carica, e ti chiedo io quando riavviare."
                if enabled else "Aggiornamenti automatici disattivati: dimmi «aggiorna il sistema» quando vuoi.")

    def connect_github_updates(token: str = "") -> str:
        from ..imageupdate import configured_repo

        if token:  # incollato in chat: riconosciuto senza modello AI (agent.SECRET_RE)
            return connect(token)
        public = public_repo()
        if public:  # repository pubblico: nessun token da chiedere
            try:
                problem = updates.try_registry(force=True)
            except Exception as exc:
                problem = str(exc)
            done = f"Non serve nessun token: il repository {public} è pubblico, le nuove versioni arrivano già da lì."
            return done + (" Gli aggiornamenti scaricheranno solo le differenze." if not problem else
                           f" Per ora come pacchetto completo: {problem}.")
        repo = load_state().get("repo") or configured_repo() or "il repository di AIOS"
        token = secret("Aggiornamenti da GitHub", "Incolla il token di sola lettura (Contents: read) per " + repo)
        if token is None:  # nessuna finestra disponibile: il token si incolla in chat (UpdatesRouter lo riconosce)
            return GITHUB_HELP.format(repo=repo, dove="incolla il token qui in chat, da solo")
        help_text = GITHUB_HELP.format(repo=repo, dove="incolla il token nella finestra che apro")
        if not token:
            return help_text + "\n\nQuando hai il token, dimmi di nuovo «collega GitHub per gli aggiornamenti»."
        return connect(token)

    def update_from_usb() -> str:
        found = updates.check(("chiavetta",))
        system = next((u for u in found if u.kind == "sistema" and updates.package is not None), None)
        if system is None:
            return ("Non trovo una versione più recente di AIOS sulla chiavetta: copia nella chiavetta i file "
                    "«aios-aggiornamento…» della Release (tutti, senza riunirli) e inseriscila.")
        if updates.busy():
            return updates.progress_text()
        updates.start_background([system])
        return (f"Ho cominciato: {system.summary}. Copio e preparo in sottofondo (qualche minuto), non togliere la "
                "chiavetta. Ti avviso io quando è pronto.")

    def restart_to_update() -> str:
        code, out = runner.run(["systemctl", "reboot"])
        return "Riavvio per applicare l'aggiornamento…" if code == 0 else f"Riavvio non riuscito: {out[-200:]}"

    variant_tool = Tool("switch_system_variant", "Passa alla versione di AIOS con il driver della scheda video NVIDIA "
                        "(per l'AI veloce sulla scheda) o torna a quella standard. Si applica al riavvio.",
                        params(variante=("Versione", ["nvidia", "standard"])), switch_variant, requires_confirmation=True)
    return [
        variant_tool,
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
                       r"|^stato\s+degli\s+aggiornamenti$"
                       r"|^(?:come\s+va|come\s+procede|a\s+che\s+punto\s+(?:è|e)|quanto\s+manca(?:\s+al(?:l')?)?)\s*"
                       r"(?:l'|con\s+l')?aggiornamento\??$")
RE_NOW = re.compile(r"^aggiorna\s+(?:il\s+sistema|il\s+computer|il\s+pc|tutto|aios)(?:\s+(?:ora|adesso|subito))?$")
RE_ROLLBACK = re.compile(r"^(?:torna|ritorna)\s+alla\s+versione\s+precedente(?:\s+del\s+sistema)?$")
RE_AUTO = re.compile(r"^(?P<v>attiva|disattiva)\s+(?:gli\s+)?aggiornamenti\s+automatici$")
RE_GITHUB = re.compile(r"^(?:collega|configura|attiva|imposta)\s+(?:a\s+)?github\b"
                       r"|\bgithub\b.*\b(?:aggiornament\w*|token)\b|\b(?:aggiornament\w*|token)\b.*\bgithub\b")
RE_TOKEN = re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,})\b")
RE_USB = re.compile(r"^aggiorna(?:\s+(?:il\s+sistema|aios))?\s+(?:dalla|con\s+la)\s+chiavetta$"
                    r"|^installa\s+l'aggiornamento\s+dalla\s+chiavetta$")
RE_RESTART = re.compile(r"^riavvia\s+per\s+aggiornare$|^applica\s+l'aggiornamento$")


RE_VARIANT = re.compile(r"^(?:passa|passare|vai|torna|installa|metti|attiva)\s+(?:alla|a|la|i|il|ai)?\s*(?:versione|variante|driver|immagine)?\s*"
                        r"(?:(?P<n>nvidia)|(?P<s>standard|senza\s+nvidia))(?:\s+(?:di|del)\s+(?:aios|sistema))?$")


class UpdatesRouter:
    def match(self, text: str) -> Intent | None:
        token = RE_TOKEN.search(text)
        if token:
            return Intent("connect_github_updates", {"token": token.group(0)})
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
        if RE_GITHUB.search(low):
            return Intent("connect_github_updates", {})
        if RE_USB.match(low):
            return Intent("update_from_usb", {})
        m = RE_VARIANT.search(low)
        if m:
            return Intent("switch_system_variant", {"variante": "standard" if m.group("s") else "nvidia"})
        return None
