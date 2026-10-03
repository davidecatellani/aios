"""Aggiornamenti automatici del sistema, senza sorprese.

- **Sistema** immutabile (rpm-ostree o bootc): il nuovo sistema si scarica e si prepara
  accanto a quello in uso, a riposo e in carica; parte al riavvio successivo, in modo
  atomico (mai «aggiornato a metà»). Mai un riavvio forzato: si avvisa e si lascia
  scegliere il momento.
- **Ritorno automatico**: se il nuovo sistema non supera i controlli all'avvio
  (greenboot, `aios-aggiornamenti verifica`), si torna da soli alla versione precedente;
  a mano: «torna alla versione precedente del sistema».
- **App** (Flatpak) aggiornate a riposo; **firmware** (fwupd) solo segnalato, perché
  di solito richiede un riavvio guidato; **modelli AI** con il loro catalogo firmato.
- Gli aggiornamenti di **sicurezza** sono evidenziati e anticipati.

    aios-aggiornamenti stato | controlla | prepara | ripristina | verifica
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .privacy import private_dir
from .tools.base import Runner

CHECK_EVERY = 12 * 3600
SECURITY_WORDS = re.compile(r"(?i)\b(?:critical|important|critica|importante|security|sicurezza|CVE-\d{4}-\d+)\b")


@dataclass
class Update:
    kind: str  # sistema | app | firmware
    summary: str
    security: bool = False
    version: str = ""
    items: list[str] = field(default_factory=list)


def state_path() -> Path:
    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios") / "aggiornamenti.json"


def load_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    state_path().write_text(json.dumps(state, ensure_ascii=False, indent=1))


# --- sistema --------------------------------------------------------------------------------------


class SystemBackend:
    """rpm-ostree (Fedora Atomic) o bootc (immagini container avviabili)."""

    def __init__(self, runner: Runner):
        self.runner = runner
        self.tool = "bootc" if runner.has("bootc") and not runner.has("rpm-ostree") else \
            "rpm-ostree" if runner.has("rpm-ostree") else ""

    def available(self) -> bool:
        return bool(self.tool)

    def status(self) -> dict[str, str]:
        """Versione in uso, preparata per il prossimo avvio, precedente."""
        if self.tool != "rpm-ostree":
            code, out = self.runner.run(["bootc", "status", "--json"]) if self.tool else (1, "")
            try:
                st = json.loads(out).get("status", {}) if code == 0 else {}
            except ValueError:
                st = {}
            pick = lambda k: ((st.get(k) or {}).get("image") or {}).get("version", "") if st.get(k) else ""  # noqa: E731
            return {"in_uso": pick("booted"), "pronta": pick("staged"), "precedente": pick("rollback")}
        code, out = self.runner.run(["rpm-ostree", "status", "--json"])
        try:
            deployments = json.loads(out).get("deployments", []) if code == 0 else []
        except ValueError:
            deployments = []
        booted = next((d for d in deployments if d.get("booted")), {})
        staged = next((d for d in deployments if d.get("staged")), {})
        others = [d for d in deployments if not d.get("booted") and not d.get("staged")]
        version = lambda d: str(d.get("version") or d.get("checksum", "")[:10]) if d else ""  # noqa: E731
        return {"in_uso": version(booted), "pronta": version(staged), "precedente": version(others[0] if others else {})}

    def check(self) -> Update | None:
        if self.tool == "rpm-ostree":
            code, out = self.runner.run(["rpm-ostree", "upgrade", "--preview"])
            if code == 77 or "No updates available" in out or code != 0:
                return None
            version = re.search(r"Version:\s*(\S+)", out)
            advisories = [l.strip() for l in out.splitlines() if SECURITY_WORDS.search(l)]
            packages = re.findall(r"^\s{2,}(\S+)\s+\S+\s*->\s*\S+", out, re.M)
            return Update("sistema", f"nuova versione del sistema{' ' + version.group(1) if version else ''}",
                          bool(advisories), version.group(1) if version else "", advisories[:5] or packages[:10])
        if self.tool == "bootc":
            code, out = self.runner.run(["bootc", "upgrade", "--check"])
            if code != 0 or "No changes" in out or "No update" in out:
                return None
            version = re.search(r"[Vv]ersion:\s*(\S+)", out)
            return Update("sistema", "nuova versione del sistema", bool(SECURITY_WORDS.search(out)),
                          version.group(1) if version else "")
        return None

    def prepare_cmd(self) -> list[str]:
        """Scarica e prepara il nuovo sistema per il prossimo avvio (quello in uso non cambia)."""
        return ["rpm-ostree", "upgrade"] if self.tool == "rpm-ostree" else ["bootc", "upgrade"]

    def rollback_cmd(self) -> list[str]:
        return ["rpm-ostree", "rollback"] if self.tool == "rpm-ostree" else ["bootc", "rollback"]


def check_apps(runner: Runner) -> Update | None:
    if not runner.has("flatpak"):
        return None
    code, out = runner.run(["flatpak", "remote-ls", "--updates", "--columns=name,application"])
    apps = [line.split("\t")[0].strip() for line in out.splitlines() if line.strip()] if code == 0 else []
    return Update("app", f"{len(apps)} app da aggiornare", False, "", apps) if apps else None


def check_firmware(runner: Runner) -> Update | None:
    if not runner.has("fwupdmgr"):
        return None
    code, out = runner.run(["fwupdmgr", "get-updates", "--json"])
    try:
        devices = json.loads(out).get("Devices", []) if code == 0 else []
    except ValueError:
        devices = []
    names = [d.get("Name", "dispositivo") for d in devices if d.get("Releases")]
    security = any(SECURITY_WORDS.search(json.dumps(d.get("Releases", []))) for d in devices)
    return Update("firmware", f"firmware da aggiornare: {', '.join(names)}", security, "", names) if names else None


# --- regista ----------------------------------------------------------------------------------------


class Updates:
    def __init__(self, runner: Runner | None = None, clock: Callable[[], float] = time.time):
        self.runner = runner or Runner()
        self.system = SystemBackend(self.runner)
        self.clock = clock

    def check(self) -> list[Update]:
        found = [u for u in (self.system.check() if self.system.available() else None,
                             check_apps(self.runner), check_firmware(self.runner)) if u]
        state = load_state()
        state.update(controllato=self.clock(), trovati=[asdict(u) for u in found])
        save_state(state)
        return found

    def prepare(self, found: list[Update] | None = None) -> list[str]:
        """Scarica e prepara tutto ciò che si può senza disturbare. → righe di esito."""
        found = self.check() if found is None else found
        report = []
        for u in found:
            if u.kind == "sistema":
                code, out = self.runner.run(self.system.prepare_cmd())
                report.append(f"Sistema: {'pronto per il prossimo riavvio' if code == 0 else 'non riuscito: ' + out[-200:]}")
                if code == 0:
                    state = load_state()
                    state["pronto"] = {"versione": u.version, "sicurezza": u.security, "quando": self.clock()}
                    save_state(state)
            elif u.kind == "app":
                code, out = self.runner.run(["flatpak", "update", "-y", "--noninteractive"])
                report.append(f"App: {'aggiornate' if code == 0 else 'non riuscito: ' + out[-200:]} ({', '.join(u.items[:5])})")
            elif u.kind == "firmware":
                report.append(f"Firmware: {u.summary} — dimmi «aggiorna il firmware» quando puoi riavviare.")
        return report

    def rollback(self) -> str:
        if not self.system.available():
            return "Questo sistema non è immutabile: il ritorno alla versione precedente non è disponibile."
        st = self.system.status()
        code, out = self.runner.run(self.system.rollback_cmd())
        if code != 0:
            return f"Non sono riuscito: {out[-200:]}"
        return (f"Fatto: al prossimo riavvio torni alla versione precedente{' (' + st['precedente'] + ')' if st['precedente'] else ''}. "
                "Dimmi «riavvia» quando vuoi.")

    def describe(self) -> str:
        state = load_state()
        lines = []
        if self.system.available():
            st = self.system.status()
            lines.append(f"Sistema in uso: {st['in_uso'] or 'sconosciuto'}.")
            if st["pronta"]:
                ready = state.get("pronto", {})
                lines.append(f"{'🔒 Aggiornamento di sicurezza' if ready.get('sicurezza') else '⬆️ Aggiornamento'} pronto "
                             f"({st['pronta']}): si applica al prossimo riavvio. Dimmi «riavvia per aggiornare» quando vuoi.")
            if st["precedente"]:
                lines.append(f"Versione precedente disponibile: {st['precedente']} («torna alla versione precedente del sistema»).")
        else:
            lines.append("Il sistema non è un'immagine AIOS immutabile: aggiorno app e firmware.")
        pending = [Update(**u) for u in state.get("trovati", []) if u["kind"] != "sistema"]
        for u in pending:
            lines.append(("🔒 " if u.security else "• ") + u.summary)
        when = state.get("controllato")
        lines.append(f"Ultimo controllo: {time.strftime('%d/%m %H:%M', time.localtime(when))}." if when else "Mai controllato.")
        lines.append("Aggiornamenti automatici: " + ("attivi (a riposo e in carica)." if auto_enabled() else "disattivati."))
        return "\n".join(lines)


def auto_enabled() -> bool:
    return load_state().get("automatici", True)


def set_auto(on: bool) -> None:
    state = load_state()
    state["automatici"] = on
    save_state(state)


# --- controllo all'avvio (greenboot) ---------------------------------------------------------------


def health_check(run: Callable[[list[str]], tuple[int, str]] | None = None) -> list[str]:
    """Controlli dopo un aggiornamento; se falliscono, greenboot torna alla versione precedente."""
    run = run or Runner().run
    problems = []
    try:
        import aios_copilot.__main__  # noqa: F401  il copilota si avvia
    except Exception as exc:
        problems.append(f"copilota: {exc}")
    for cmd, what in ((["systemctl", "is-system-running", "--wait"], "servizi di sistema"),):
        code, out = run(cmd)
        if code != 0 and "degraded" not in out:
            problems.append(f"{what}: {out.strip()[-120:]}")
    return problems


# --- compito a riposo ------------------------------------------------------------------------------


@dataclass
class UpdateTask:
    """Nel pianificatore a riposo: controlla due volte al giorno e prepara ciò che trova.

    Il lavoro (scaricare, preparare) gira in un thread: lo svolgono i servizi di sistema
    (rpm-ostreed, flatpak) a bassa priorità, e il pianificatore resta libero.
    """

    updates: Updates | None = None
    notify: Callable[[str, str], None] | None = None
    name: str = "aggiornamenti del sistema"
    _retry_at: float = 0.0
    _thread: threading.Thread | None = None

    def available(self) -> bool:
        return time.monotonic() >= self._retry_at

    def has_work(self) -> bool:
        busy = self._thread is not None and self._thread.is_alive()
        return busy or (auto_enabled() and time.time() - float(load_state().get("controllato", 0)) > CHECK_EVERY)

    def step(self, seconds: float) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._work, daemon=True)
            self._thread.start()
        self._thread.join(seconds)

    def _work(self) -> None:
        updates = self.updates or Updates()
        try:
            found = updates.check()
            if found:
                updates.prepare(found)
        except Exception:
            self._retry_at = time.monotonic() + 3600
            return
        system = next((u for u in found if u.kind == "sistema"), None)
        if system is not None and self.notify is not None:
            title = "🔒 Aggiornamento di sicurezza pronto" if system.security else "⬆️ Aggiornamento di AIOS pronto"
            self.notify(title, "Si applica al prossimo riavvio, quando vuoi tu. Dimmi «riavvia per aggiornare».")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv) or ["stato"]
    u = Updates()
    if args[0] == "stato":
        print(u.describe())
    elif args[0] == "controlla":
        found = u.check()
        print("\n".join(("🔒 " if x.security else "• ") + x.summary for x in found) or "Tutto aggiornato.")
    elif args[0] == "prepara":
        print("\n".join(u.prepare()) or "Niente da aggiornare.")
    elif args[0] == "ripristina":
        print(u.rollback())
    elif args[0] == "verifica":
        problems = health_check()
        print("\n".join(problems) or "Tutto a posto.")
        return 1 if problems else 0
    else:
        print("aios-aggiornamenti [stato | controlla | prepara | ripristina | verifica]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
