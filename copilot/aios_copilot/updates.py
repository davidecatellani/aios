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
- Immagine AIOS **privata**: le nuove versioni arrivano dal registro di GitHub, solo gli strati
  cambiati (registro.py), oppure come pacchetto completo dalla Release o da una chiavetta
  (imageupdate.py), sempre con il permesso di sola lettura dell'utente.

    aios-aggiornamenti stato | controlla | prepara | ripristina | verifica | github | chiavetta
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
REGISTRY_SWITCH = "registro"  # versione segnaposto: il primo passaggio agli aggiornamenti dal registro
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

    def from_registry(self, ref: str) -> bool:
        """Il sistema in uso (o quello pronto) arriva già dal registro delle immagini?"""
        from .registro import on_registry

        if self.tool == "rpm-ostree":
            code, out = self.runner.run(["rpm-ostree", "status", "--json"])
            return code == 0 and on_registry(out, ref)
        if self.tool == "bootc":
            code, out = self.runner.run(["bootc", "status", "--json"])
            return code == 0 and ref in out
        return False

    def check_registry(self) -> Update | None:
        """Nuova versione nel registro? (si confrontano gli strati, non si scarica niente)"""
        if self.tool != "rpm-ostree":
            return self.check()
        code, out = self.runner.run(["rpm-ostree", "upgrade", "--check"])
        if code != 0 or "No updates available" in out:
            return None  # 77: niente di nuovo
        version = re.search(r"Version:\s*(\S+)", out)
        return Update("sistema", f"AIOS {version.group(1) if version else 'nuovo'} (dal registro, solo le differenze)",
                      bool(SECURITY_WORDS.search(out)), version.group(1) if version else "")

    def prepare_cmd(self) -> list[str]:
        """Scarica e prepara il nuovo sistema per il prossimo avvio (quello in uso non cambia)."""
        return ["rpm-ostree", "upgrade"] if self.tool == "rpm-ostree" else ["bootc", "upgrade"]

    def rollback_cmd(self) -> list[str]:
        return ["rpm-ostree", "rollback"] if self.tool == "rpm-ostree" else ["bootc", "rollback"]


def check_apps(runner: Runner) -> Update | None:
    """App da aggiornare, sia quelle per l'utente (come le installa Nova) sia quelle di sistema."""
    if not runner.has("flatpak"):
        return None
    apps: list[str] = []
    for where in ("--user", "--system"):
        code, out = runner.run(["flatpak", "remote-ls", where, "--updates", "--app", "--columns=name,application"])
        for line in out.splitlines() if code == 0 else []:
            name = line.split("\t")[0].strip()
            if name and name not in apps:
                apps.append(name)
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
    def __init__(self, runner: Runner | None = None, clock: Callable[[], float] = time.time,
                 github: Callable[[], Any] | None = None, media: Callable[[], Any] | None = None,
                 version: Callable[[], str] | None = None):
        from . import imageupdate

        self.runner = runner or Runner()
        self.system = SystemBackend(self.runner)
        self.clock = clock
        self.github = github or github_source  # → GithubSource o None (non collegato)
        self.media = media or imageupdate.find_on_media
        self.version = version or imageupdate.installed_version
        self.package: Any = None  # nuova versione trovata su GitHub o sulla chiavetta

    def find_package(self, sources: tuple[str, ...] = ("chiavetta", "github")) -> Any:
        """La versione più recente di AIOS disponibile (prima la chiavetta: è gratis e veloce)."""
        from .imageupdate import newer

        current = self.version()
        if not current:
            return None  # non è un'immagine AIOS
        best = None
        for source in sources:
            try:
                if source == "chiavetta":
                    pkg = self.media()
                else:
                    gh = self.github()
                    pkg = gh.latest() if gh is not None else None
            except Exception:
                pkg = None
            if pkg is not None and newer(pkg.version, current) and (best is None or newer(pkg.version, best.version)):
                best = pkg
        return best

    def check(self, sources: tuple[str, ...] = ("chiavetta", "github")) -> list[Update]:
        system = None
        if self.system.available():
            self.try_registry()
        ref = load_state().get("registro") if self.system.available() else None
        if ref and self.system.from_registry(ref):
            system = self.system.check_registry()  # incrementale: solo gli strati cambiati
        elif ref and "github" in sources:
            system = Update("sistema", "AIOS dal registro di GitHub: questa volta si scarica tutto, poi solo le "
                                       "differenze", False, REGISTRY_SWITCH)
        elif self.system.available():
            self.package = self.find_package(sources)
            if self.package is not None:
                p = self.package
                system = Update("sistema", f"AIOS {p.version} ({'dalla chiavetta' if p.origin == 'chiavetta' else 'da GitHub'}, "
                                           f"{p.size / 1e9:.1f} GB)", False, p.version)
            elif not self.version():
                system = self.system.check()
        found = [u for u in (system, check_apps(self.runner), check_firmware(self.runner)) if u]
        state = load_state()
        state.update(controllato=self.clock(), trovati=[asdict(u) for u in found])
        save_state(state)
        return found

    def prepare(self, found: list[Update] | None = None) -> list[str]:
        """Scarica e prepara tutto ciò che si può senza disturbare. → righe di esito."""
        found = self.check() if found is None else found
        report = []
        for u in found:
            if u.kind == "sistema" and u.version == REGISTRY_SWITCH:
                report.append(self._switch_to_registry())
            elif u.kind == "sistema" and self.package is not None:
                report.append(self._prepare_package(u))
            elif u.kind == "sistema":
                code, out = self.runner.run(self.system.prepare_cmd())
                report.append(f"Sistema: {'pronto per il prossimo riavvio' if code == 0 else 'non riuscito: ' + out[-200:]}")
                if code == 0:
                    state = load_state()
                    state["pronto"] = {"versione": u.version, "sicurezza": u.security, "quando": self.clock()}
                    save_state(state)
            elif u.kind == "app":
                results = [self.runner.run(["flatpak", "update", where, "-y", "--noninteractive"])
                           for where in ("--user", "--system")]
                ok = any(code == 0 for code, _ in results)
                report.append(f"App: {'aggiornate' if ok else 'non riuscito: ' + results[-1][1][-200:]} ({', '.join(u.items[:5])})")
                if ok:
                    state = load_state()
                    state["app_aggiornate"] = {"quando": self.clock(), "nomi": u.items[:10]}
                    save_state(state)
            elif u.kind == "firmware":
                report.append(f"Firmware: {u.summary} — dimmi «aggiorna il firmware» quando puoi riavviare.")
        return report

    def _prepare_package(self, u: Update) -> str:
        from .imageupdate import assemble, stage_command

        pkg = self.package
        try:
            archive = assemble(pkg)
        except (OSError, ValueError) as exc:
            return f"Sistema: AIOS {pkg.version} non preparato: {exc}"
        code, out = self.runner.run(stage_command(self.system.tool, archive))
        if code != 0:
            # il file scaricato resta: al prossimo tentativo non si riscaricano gigabyte
            if "not allowed" in out or "Not authorized" in out:
                return (f"Sistema: AIOS {pkg.version} è scaricato ma il sistema non mi dà il permesso di installarlo. "
                        "Serve una regola di AIOS che manca in questa versione: chiedi all'assistenza il comando, "
                        "poi dimmi di nuovo «aggiorna il sistema» (non riscarico niente).")
            return f"Sistema: AIOS {pkg.version} non preparato: {out[-200:]}"
        archive.unlink(missing_ok=True)  # ormai è nel sistema
        state = load_state()
        state["pronto"] = {"versione": pkg.version, "sicurezza": u.security, "quando": self.clock()}
        save_state(state)
        return f"Sistema: AIOS {pkg.version} pronto per il prossimo riavvio (dati, impostazioni e app restano)."

    # --- in sottofondo, con l'avanzamento visibile ----------------------------------------------------
    _worker: threading.Thread | None = None

    def busy(self) -> bool:
        return Updates._worker is not None and Updates._worker.is_alive()

    def start_background(self, found: list[Update], notify: Callable[[str, str], None] | None = None) -> None:
        from .agenda import notify as default_notify

        notify = notify or default_notify
        state = load_state()
        system = next((u for u in found if u.kind == "sistema"), None)
        state["in_corso"] = {"cosa": system.summary if system else "aggiornamenti", "inizio": self.clock(),
                             "totale": self.package.size if self.package is not None else 0}
        save_state(state)

        def work() -> None:
            try:
                report = self.prepare(found)
            except Exception as exc:  # l'utente deve sempre sapere com'è finita
                report = [f"Aggiornamento non riuscito: {exc}"]
            state = load_state()
            state.pop("in_corso", None)
            state["ultimo_esito"] = report
            save_state(state)
            notify("Aggiornamento di AIOS", "\n".join(report)[:300])

        Updates._worker = threading.Thread(target=work, daemon=True)
        Updates._worker.start()

    def progress_text(self) -> str:
        from .imageupdate import workdir

        job = load_state().get("in_corso") or {}
        total = job.get("totale") or 0
        done = 0
        try:
            done = sum(p.stat().st_size for p in workdir().iterdir() if p.is_file())
        except OSError:
            pass
        minutes = int((self.clock() - job.get("inizio", self.clock())) / 60)
        if total and done:
            pct = min(99, int(done * 100 / (2 * total if done > total else total)))
            phase = "ricompongo l'immagine" if done > total else f"scaricati {done / 1e9:.1f} di {total / 1e9:.1f} GB"
            return f"Aggiornamento in corso ({pct}%): {phase}, da {minutes} minuti. Ti avviso quando è pronto."
        return f"Aggiornamento in corso da {minutes} minuti ({job.get('cosa', 'sistema')}). Ti avviso quando è pronto."

    def try_registry(self, force: bool = False) -> str:
        """Si prova il registro (al massimo una volta al giorno): senza accesso se l'immagine è pubblica, con il
        token salvato se è privata. → "" se va."""
        from . import vault
        from .imageupdate import TOKEN_KEY, configured_repo
        from .registro import image_ref

        state = load_state()
        if state.get("registro") and not force:
            return ""
        if not force and self.clock() - state.get("registro_provato", 0) < 86400:
            return state.get("registro_motivo", "")
        repo = state.get("repo") or configured_repo()
        if not repo:
            return "non so da quale repository prendere gli aggiornamenti"
        token = vault.load(TOKEN_KEY) or ""
        access = self.registry_access(repo, token)
        problem = access.check()
        if not problem and token and not access.hand_over():  # immagine pubblica: nessun accesso da installare
            problem = "il servizio che installa l'accesso non ha risposto"
        state = load_state()
        state["registro_provato"] = self.clock()
        if problem:
            state["registro_motivo"] = problem
        else:
            state.pop("registro_motivo", None)
            state["registro"] = image_ref(repo)
        save_state(state)
        return problem

    def registry_access(self, repo: str, token: str) -> Any:
        from .registro import Access

        return Access(repo, token)

    def _switch_to_registry(self) -> str:
        from .registro import rebase_command

        ref = load_state().get("registro", "")
        code, out = self.runner.run(rebase_command(self.system.tool, ref))
        if code != 0:
            return f"Sistema: passaggio al registro non riuscito: {out[-200:]}"
        state = load_state()
        state["pronto"] = {"versione": "registro", "sicurezza": False, "quando": self.clock()}
        save_state(state)
        return ("Sistema: AIOS pronto per il prossimo riavvio. Da adesso gli aggiornamenti scaricano solo le "
                "differenze.")

    def switch_variant(self, variant: str, lspci: Callable[[], str] | None = None) -> str:
        """Passa alla variante dell'immagine con (nvidia) o senza (standard) il driver NVIDIA, dal registro.
        Si prepara accanto al sistema in uso e si applica al riavvio; dati e app restano."""
        from .imageupdate import configured_repo, installed_variant
        from .registro import Access, image_ref, rebase_command

        variant = "nvidia" if "nvidia" in variant.lower() else "standard"
        if not self.system.available():
            return "Questo sistema non è un'immagine AIOS: la variante non si può cambiare da qui."
        if installed_variant() == variant:
            return f"Il sistema è già la versione {'NVIDIA' if variant == 'nvidia' else 'standard'}."
        if variant == "nvidia":
            found = (lspci or (lambda: self.runner.run(["lspci"])[1]))()
            if "nvidia" not in found.lower():
                return "Non vedo una scheda video NVIDIA in questo PC: la versione NVIDIA non servirebbe."
        repo = load_state().get("repo") or configured_repo()
        ref = image_ref(repo, variant=variant)
        problem = Access(repo).check(ref.rsplit(":", 1)[1])
        if problem:
            return f"Non posso ancora passare alla versione {variant}: {problem}."
        code, out = self.runner.run(rebase_command(self.system.tool, ref))
        if code != 0:
            return f"Passaggio non riuscito: {out[-200:]}"
        state = load_state()
        state["registro"] = ref
        state["pronto"] = {"versione": f"variante {variant}", "sicurezza": False, "quando": self.clock()}
        save_state(state)
        note = (" Prima di riavviare disattiva il Secure Boot nel BIOS: il driver NVIDIA di AIOS non è firmato."
                if variant == "nvidia" else "")
        return f"Pronto: al prossimo riavvio AIOS passa alla versione {'NVIDIA' if variant == 'nvidia' else 'standard'}.{note}"

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
        lines = [self.progress_text()] if self.busy() else []
        if not self.busy() and state.get("ultimo_esito"):
            lines.append("Ultimo aggiornamento: " + " ".join(state["ultimo_esito"])[:300])
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
        if self.version():
            lines.append(f"Versione di AIOS: {self.version()}.")
            gh = self.github()
            lines.append("Nuove versioni da GitHub: " + ("non configurato; oppure da chiavetta." if gh is None else
                         "collegato con il tuo accesso." if gh.token else "dal repository pubblico, senza accesso."))
            if state.get("registro"):
                lines.append("Aggiornamenti incrementali dal registro: attivi (si scaricano solo le differenze).")
            elif state.get("registro_motivo") and self.github() is not None:
                lines.append(f"Aggiornamenti incrementali: non ancora, {state['registro_motivo']}. "
                             "Intanto arrivano come pacchetto completo.")
        when = state.get("controllato")
        lines.append(f"Ultimo controllo: {time.strftime('%d/%m %H:%M', time.localtime(when))}." if when else "Mai controllato.")
        lines.append("Aggiornamenti automatici: " + ("attivi (a riposo e in carica)." if auto_enabled() else "disattivati."))
        return "\n".join(lines)


def github_source() -> Any:
    from . import vault
    from .imageupdate import TOKEN_KEY, GithubSource, configured_repo

    repo = load_state().get("repo") or configured_repo()
    return GithubSource(repo, vault.load(TOKEN_KEY) or "") if repo else None  # pubblico: senza token


def public_repo(repo: str = "") -> str:
    """Il repository degli aggiornamenti, se si legge senza accesso (pubblico); altrimenti ""."""
    from .imageupdate import GithubSource, configured_repo

    state = load_state()
    repo = repo or state.get("repo") or configured_repo()
    if not repo:
        return ""
    seen = state.get("pubblico") or {}
    if seen.get("repo") == repo and time.time() - seen.get("quando", 0) < 86400:
        return repo if seen.get("si") else ""
    problem = GithubSource(repo).check_access()
    if problem.startswith("GitHub non raggiungibile"):
        return ""  # senza rete non si sa: si riprova la prossima volta
    state = load_state()
    state["pubblico"] = {"repo": repo, "si": not problem, "quando": time.time()}
    save_state(state)
    return "" if problem else repo


def connect_github(token: str, repo: str = "") -> str:
    """Salva il permesso di sola lettura dell'utente, dopo averlo provato."""
    from . import vault
    from .imageupdate import TOKEN_KEY, GithubSource, configured_repo

    repo = repo or load_state().get("repo") or configured_repo()
    if not repo:
        return "Non so da quale repository prendere gli aggiornamenti."
    token = token.strip()
    if not token:
        return "Nessun token inserito."
    problem = GithubSource(repo, token).check_access()
    if problem:
        return f"Non ha funzionato: {problem}."
    vault.store(TOKEN_KEY, token)
    state = load_state()
    state["repo"] = repo
    state.pop("registro", None)
    save_state(state)
    done = f"Collegato: le nuove versioni di AIOS arriveranno da {repo}, con il tuo accesso di sola lettura."
    try:
        problem = Updates().try_registry(force=True)
    except Exception as exc:
        problem = str(exc)
    if not problem:
        return done + " Gli aggiornamenti scaricheranno solo le differenze."
    return done + f" Per ora come pacchetto completo: {problem}."


GITHUB_HELP = (
    "Per scaricare gli aggiornamenti dal tuo repository privato mi serve un permesso di sola lettura:\n"
    "1. apri https://github.com/settings/personal-access-tokens/new\n"
    "2. nome «AIOS aggiornamenti», scadenza a tua scelta; «Repository access» › «Only select repositories» › {repo};\n"
    "3. «Permissions» › «Contents» › «Read-only» (nient'altro), poi «Generate token»;\n"
    "4. incolla il token nella finestra che apro (resta nel portachiavi del PC, non passa dal modello AI);\n   se la finestra non si apre, incollalo qui in chat: lo riconosco senza modello AI.\n"
    "Per aggiornamenti piccoli (solo le differenze) serve invece un token «classico» "
    "(https://github.com/settings/tokens/new) con i permessi «repo» e «read:packages».")


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
    # Solo ciò che rende AIOS inutilizzabile: un servizio che aspetta internet o un controllo che
    # richiede la rete (senza Wi-Fi) non deve far riavviare il PC.
    code, out = run(["systemctl", "is-failed", "ollama.service"])
    if code == 0 and out.strip() == "failed":
        problems.append("Ollama (modelli AI) non parte")
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
        report: list[str] = []
        try:
            found = updates.check()
            if found:
                report = updates.prepare(found)
        except Exception as exc:
            self._retry_at = time.monotonic() + 3600
            if self.notify is not None:
                self.notify("Aggiornamenti", f"Non sono riuscita a controllare gli aggiornamenti: {exc}")
            return
        if self.notify is not None:
            for title, text in announcements(found, report):
                self.notify(title, text)


def announcements(found: list[Update], report: list[str]) -> list[tuple[str, str]]:
    """Cosa dire all'utente dopo un giro di aggiornamenti automatici (niente se non è cambiato niente)."""
    out: list[tuple[str, str]] = []
    system = next((u for u in found if u.kind == "sistema"), None)
    sys_line = next((r for r in report if r.startswith("Sistema")), "")
    if system is not None and "pronto" in sys_line:
        out.append(("Aggiornamento di sicurezza pronto" if system.security else "Nuova versione di AIOS pronta",
                    "Si applica al prossimo riavvio, quando vuoi tu: dimmi «riavvia per aggiornare»."))
    elif system is not None and sys_line:
        out.append(("Aggiornamento di AIOS non riuscito", sys_line.removeprefix("Sistema: ")[:240]))
    apps = next((u for u in found if u.kind == "app"), None)
    app_line = next((r for r in report if r.startswith("App")), "")
    if apps is not None and "aggiornate" in app_line:
        names = ", ".join(apps.items[:4]) + (f" e altre {len(apps.items) - 4}" if len(apps.items) > 4 else "")
        out.append(("App aggiornate", f"Ho aggiornato {names}. Se una era aperta, la versione nuova parte alla prossima apertura."))
    elif apps is not None and app_line:
        out.append(("Aggiornamento delle app non riuscito", app_line.removeprefix("App: ")[:240]))
    firmware = next((u for u in found if u.kind == "firmware"), None)
    if firmware is not None:
        out.append(("Aggiornamento del firmware", firmware.summary.capitalize() + ": dimmi «aggiorna il firmware» quando puoi riavviare."))
    return out


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
    elif args[0] == "github":
        import getpass

        from .imageupdate import configured_repo

        print(GITHUB_HELP.format(repo=load_state().get("repo") or configured_repo() or "il repository di AIOS",
                                 dove="incolla il token qui sotto"))
        print(connect_github(getpass.getpass("Token: ")))
    elif args[0] == "chiavetta":
        found = u.check(("chiavetta",))
        system = next((x for x in found if x.kind == "sistema" and u.package is not None), None)
        if system is None:
            if "--avvisa" not in args:
                print("Sulla chiavetta non c'è una versione di AIOS più recente di quella in uso.")
            return 0
        if "--avvisa" in args:
            from .mesh.service import notify_with_actions

            if notify_with_actions("💾 Aggiornamento di AIOS sulla chiavetta", f"{system.summary}. Lo preparo? "
                                   "Si applica al prossimo riavvio, i tuoi dati restano.",
                                   {"installa": "Prepara", "no": "Non ora"}) != "installa":
                return 0
        print("\n".join(u.prepare([system])))
    elif args[0] == "verifica":
        problems = health_check()
        print("\n".join(problems) or "Tutto a posto.")
        return 1 if problems else 0
    else:
        print("aios-aggiornamenti [stato | controlla | prepara | ripristina | verifica | github | chiavetta]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
