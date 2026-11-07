"""Le procedure a voce (procedure.py, docs/PROCEDURE.md): «ogni mese voglio il resoconto delle spese», «fai il
resoconto spese», «a che punto è il resoconto?», «vai avanti», «rifai i conti del resoconto», «le mie procedure».

Una procedura gira in sottofondo (cede il passo all'utente, precedenza.py) e avvisa quando ha finito o quando una
fase è da guardare. Quelle con un «quando» partono da sole (start_scheduler).
"""

from __future__ import annotations

import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .. import procedure, procedure_crea
from ..fastpath import Intent, normalize
from .base import Tool, params

_running: dict[str, threading.Thread] = {}
_lock = threading.Lock()


def _default_notify(title: str, text: str) -> None:
    from ..agenda import notify

    notify(title, text)


def _status_line(ws: procedure.Workspace, runner: procedure.Runner) -> str:
    states = runner.status()
    plan = procedure.describe_when(procedure.schedule(ws))
    if ws.name in _running and _running[ws.name].is_alive():
        now = "in corso"
    elif any(label == procedure.REVIEW for _, label in states):
        st = next(s for s, label in states if label == procedure.REVIEW)
        now = f"ferma alla fase «{st.name}», da guardare"
    elif any(label == procedure.STOPPED for _, label in states):
        st = next(s for s, label in states if label == procedure.STOPPED)
        now = f"ferma alla fase «{st.name}» per un problema"
    elif states and all(label == procedure.DONE for _, label in states):
        now = f"finita: il risultato è in stages/{states[-1][0].key}/output/"
    else:
        now = "da fare"
    waiting = [(k, v["regola"]) for k, v in procedure.proposals(ws).items() if not v.get("deciso")]
    if waiting:
        now += "; proposta per le istruzioni: " + "; ".join(f"«{rule}»" for _, rule in waiting)
    return f"«{ws.name}» ({plan}): {now}"


def make_tools(model: Callable[[], Any] = lambda: None, tools: Callable[[], dict[str, Any]] = dict,
               confirm: Callable[[str, dict[str, Any]], bool] = lambda name, args: False,
               base: Callable[[], Path] = procedure.root, notify: Callable[[str, str], None] | None = None,
               background: bool = True) -> list[Tool]:
    notify = notify or _default_notify

    def runner(ws: procedure.Workspace) -> procedure.Runner:
        return procedure.Runner(ws, model(), tools(), confirm=confirm, notify=notify)

    def go(ws: procedure.Workspace, work: Callable[[procedure.Runner], procedure.Report]) -> str:
        """Fa partire il lavoro (in sottofondo): → la frase per l'utente."""
        with _lock:
            if ws.name in _running and _running[ws.name].is_alive():
                return f"«{ws.name}» sta già lavorando: ti avviso quando c'è qualcosa da guardare."

            def job() -> None:
                try:
                    report = work(runner(ws))
                    if report.status == "finita":
                        notify(f"Procedura «{ws.name}»", report.message)
                except Exception as exc:  # un errore non deve far cadere Nova: si dice e basta
                    notify(f"Procedura «{ws.name}»", f"Si è fermata per un errore: {exc}")
                finally:
                    _running.pop(ws.name, None)

            if not background:
                job()
                return _status_line(ws, runner(ws))
            thread = threading.Thread(target=job, daemon=True, name=f"procedura-{ws.name}")
            _running[ws.name] = thread
            thread.start()
        return (f"Ho avviato «{ws.name}»: lavora in sottofondo e cede il passo a te. "
                "Ti avviso quando è finita o se c'è da guardare qualcosa.")

    def pick(nome: str) -> tuple[procedure.Workspace | None, str]:
        if not nome.strip():
            waiting = [ws for ws in procedure.procedures(base()) if any(l == procedure.REVIEW for _, l in runner(ws).status())]
            if len(waiting) == 1:
                return waiting[0], ""
            return None, "Quale procedura? " + list_procedures()
        ws = procedure.find(nome, base())
        return (ws, "") if ws else (None, f"Non trovo una procedura «{nome}». " + list_procedures())

    def list_procedures() -> str:
        found = procedure.procedures(base())
        if not found:
            return ("Non hai ancora procedure. Dimmi per esempio «ogni mese voglio il resoconto delle spese» o "
                    "«ogni lunedì le cose da fare dalla posta».")
        return "Le tue procedure:\n" + "\n".join("• " + _status_line(ws, runner(ws)) for ws in found)

    def create_procedure(cosa: str, fasi: str = "", nome: str = "", quando: str = "") -> str:
        steps = [s.strip(" -•") for s in re.split(r"\n|;|\s*\d+[.)]\s+", fasi) if s.strip(" -•")]
        model_name = None if steps else procedure_crea.which_model(cosa)
        if not steps and not model_name:
            return ("Dimmi le fasi, in ordine (per esempio: «prendi le bollette; somma per mese; scrivi il riepilogo»), "
                    "oppure parti da un modello pronto: resoconto spese, cose da fare dalla posta, preventivo.")
        try:
            ws = procedure_crea.create(base(), name=nome, purpose=cosa, steps=steps, model=model_name or "",
                                       when=quando or None if model_name else quando)
        except FileExistsError as exc:
            return f"Non la creo: {exc}. Vuoi avviarla?"
        stages = ws.stages()
        lines = [f"Ho creato la procedura «{ws.name}» in Procedure/{ws.name}, con {len(stages)} fasi: "
                 + ", ".join(st.name for st in stages) + ".",
                 f"Quando parte: {procedure.describe_when(procedure.schedule(ws))}.",
                 "Mi fermo a farti vedere il risultato della prima e dell'ultima fase."]
        if model_name == "spese":
            lines.append("Metti l'estratto conto del mese (CSV o PDF) nella cartella input della procedura.")
        elif model_name == "preventivo":
            lines.append("Scrivi il tuo listino in _config/listino.md e metti la richiesta del cliente nella cartella input.")
        lines.append("Dimmi «fai " + ws.name.lower() + "» quando vuoi provarla.")
        return "\n".join(lines)

    def run_procedure(nome: str) -> str:
        ws, problem = pick(nome)
        if ws is None:
            return problem
        procedure.mark_started(ws)
        return go(ws, lambda r: r.run())

    def procedure_status(nome: str = "") -> str:
        if not nome.strip():
            return list_procedures()
        ws, problem = pick(nome)
        if ws is None:
            return problem
        r = runner(ws)
        lines = [_status_line(ws, r)] + [f"  {i}. {st.name}: {label}" for i, (st, label) in enumerate(r.status(), 1)]
        problems = [s.get("problems") or [s.get("message", "")] for s in ws.load_state()["stages"].values()
                    if s.get("status") in (procedure.REVIEW, procedure.STOPPED) and (s.get("problems") or s.get("message"))]
        if problems:
            lines.append("Da sistemare: " + "; ".join(p for ps in problems for p in ps if p))
        return "\n".join(lines)

    def continue_procedure(nome: str = "") -> str:
        ws, problem = pick(nome)
        if ws is None:
            return problem
        return go(ws, lambda r: r.proceed())

    def redo_procedure(nome: str, fase: str) -> str:
        ws, problem = pick(nome)
        if ws is None:
            return problem
        want = normalize(fase)
        st = next((s for s in ws.stages() if want and (want in s.name.lower() or want in s.key.lower()
                                                        or s.name.lower() in want)), None)
        if st is None:
            return f"«{ws.name}» non ha una fase «{fase}». Le fasi sono: " + ", ".join(s.name for s in ws.stages()) + "."
        runner(ws).redo(st.key)
        return go(ws, lambda r: r.run())

    def procedure_rule(nome: str = "", decisione: str = "sì", fase: str = "") -> str:
        """Le correzioni che diventano istruzioni: sì o no alla regola proposta."""
        if nome.strip():
            ws, problem = pick(nome)
            if ws is None:
                return problem
            candidates = [ws]
        else:
            candidates = [w for w in procedure.procedures(base()) if any(not v.get("deciso") for v in procedure.proposals(w).values())]
            if len(candidates) != 1:
                return "Non c'è una proposta che aspetta." if not candidates else "Per quale procedura? " + list_procedures()
        ws = candidates[0]
        pending = [k for k, v in procedure.proposals(ws).items() if not v.get("deciso")]
        if fase.strip():
            pending = [k for k in pending if normalize(fase) in k.lower()] or pending
        if not pending:
            return f"«{ws.name}» non ha proposte da decidere."
        yes = not re.match(r"^\s*(no|non|lascia)", decisione.lower())
        return procedure.decide(ws, pending[0], yes)

    def schedule_procedure(nome: str, quando: str) -> str:
        ws, problem = pick(nome)
        if ws is None:
            return problem
        if re.search(r"\b(mai|solo quando|a mano|togli)\b", quando.lower()):
            procedure.set_schedule(ws, None)
            return f"«{ws.name}» parte solo quando lo chiedi."
        plan = procedure.parse_when(quando)
        if plan is None:
            return "Quando? Per esempio «ogni mese il 1 alle 9», «ogni lunedì alle 8», «ogni giorno alle 18»."
        procedure.set_schedule(ws, plan)
        return f"Fatto: «{ws.name}» parte {procedure.describe_when(plan)}."

    return [
        Tool("list_procedures", "Elenca le procedure dell'utente (lavori ripetuti fatti a fasi) e a che punto sono.",
             params([]), list_procedures),
        Tool("create_procedure", "Crea una procedura: un lavoro ripetuto fatto a fasi (resoconto spese mensile, cose da "
             "fare dalla posta, preventivi, o fasi dette dall'utente). Prima chiedi cosa deve uscire, da dove prende i dati, "
             "quando parte e se vuole vedere il risultato prima.",
             params(["cosa"], cosa="Cosa fa la procedura, con le parole dell'utente",
                    fasi="Le fasi in ordine, separate da «;» (vuoto per i modelli pronti: spese, posta, preventivo)",
                    nome="Nome breve", quando="Quando parte da sola, es. «ogni mese il 1 alle 9» (vuoto: a mano)"),
             create_procedure),
        Tool("run_procedure", "Avvia una procedura dell'utente (lavora in sottofondo e avvisa).",
             params(["nome"], nome="Nome o parole della procedura"), run_procedure),
        Tool("procedure_status", "Dice a che punto è una procedura (o tutte).",
             params([], nome="Nome o parole della procedura (vuoto: tutte)"), procedure_status, reads_private=True),
        Tool("continue_procedure", "Fa andare avanti una procedura ferma a una fase da guardare («vai avanti»).",
             params([], nome="Nome o parole della procedura (vuoto: quella che aspetta)"), continue_procedure),
        Tool("redo_procedure", "Rifà una fase di una procedura e quelle dopo.",
             params(["nome", "fase"], nome="Nome o parole della procedura", fase="La fase da rifare"), redo_procedure),
        Tool("procedure_rule", "Decide se la regola imparata dalle correzioni dell'utente va scritta nelle istruzioni di una "
             "fase («sì, scrivila nelle istruzioni» / «no, lascia stare»).",
             params([], nome="Nome della procedura (vuoto: quella con la proposta)", decisione="«sì» o «no»",
                    fase="La fase (vuoto: quella della proposta)"), procedure_rule),
        Tool("schedule_procedure", "Decide quando una procedura parte da sola (o «solo a mano»).",
             params(["nome", "quando"], nome="Nome o parole della procedura", quando="es. «ogni mese il 1 alle 9»"),
             schedule_procedure),
    ]


def _only_one(lock: Path | None) -> bool:
    """Un solo processo fa partire le procedure pianificate: chi prende per primo il lucchetto (resta suo finché vive)."""
    import fcntl
    import os

    path = lock or Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"aios-procedure-{os.getuid()}.lock"
    try:
        handle = open(path, "a")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    _only_one.handle = handle  # type: ignore[attr-defined]  # aperto finché vive il processo
    return True


def start_scheduler(tools: list[Tool], base: Callable[[], Path] = procedure.root, every: float = 300.0,
                    sleep: Callable[[float], None] = time.sleep, lock: Path | None = None) -> threading.Thread | None:
    """Le procedure con un «quando» partono da sole (controllo ogni 5 minuti). Una volta sola per processo."""
    if getattr(start_scheduler, "started", False) or not _only_one(lock):
        return None  # c'è già (in questo processo o in un altro Nova aperto: shell, voce, terminale)
    start_scheduler.started = True  # type: ignore[attr-defined]
    run = next(t for t in tools if t.name == "run_procedure").func

    def loop() -> None:
        while True:
            try:
                for ws in procedure.due(base()):
                    run(ws.name)
            except Exception:
                pass  # la cartella non c'è ancora, un file illeggibile…: si riprova tra poco
            sleep(every)

    thread = threading.Thread(target=loop, daemon=True, name="procedure-pianificate")
    thread.start()
    return thread


# --- le frasi riconosciute senza modello -------------------------------------------------------------------
RE_LIST = re.compile(r"^(?:le mie procedure|che procedure ho|elenca le procedure|mostrami le procedure)$")
RE_RUN = re.compile(r"^(?:fai|esegui|avvia|lancia|prepara|parti con)\s+(?:la procedura\s+)?(?P<n>.+)$")
RE_STATUS = re.compile(r"^a che punto (?:è|e'|e|sta|sono)\s+(?:la procedura\s+)?(?P<n>.+)$")
RE_GO_ON = re.compile(r"^(?:vai avanti|continua|prosegui|va bene,? vai avanti)(?:\s+con\s+(?:la procedura\s+)?(?P<n>.+))?$")
RE_RULE = re.compile(r"^(?:(?P<si>s[iì],?\s+)?scriv(?:i|ila|ilo|i la regola)\s+nelle istruzioni|(?P<no>no,?\s+)?lascia (?:stare|le istruzioni come sono))"
                     r"(?:\s+(?:di|del|della|dello)\s+(?P<n>.+))?$")
RE_REDO = re.compile(r"^rifai\s+(?:la fase\s+)?(?P<f>.+?)\s+(?:del|della|dello|dei|delle|di)\s+(?P<n>.+)$")
STOP = {"procedura", "della", "delle", "dello", "dalla", "dalle", "degli", "con", "per", "mia", "mio", "il", "lo", "la"}


def _words(text: str) -> set[str]:
    return {w.rstrip("aeio") for w in re.findall(r"\w+", text.lower()) if len(w) > 2 and w not in STOP}


class ProceduresRouter:
    """Le frasi chiare sulle procedure. «Fai/avvia X» vale solo se X è davvero il nome di una procedura (almeno metà
    delle sue parole), così «avvia Spotify» o «apri la posta» restano alle app e alla posta."""

    def __init__(self, base: Callable[[], Path] = procedure.root):
        self.base = base

    def _named(self, text: str, strict: bool) -> procedure.Workspace | None:
        ws = procedure.find(text, self.base())
        if ws is None:
            return None
        title = _words(ws.name)
        hits = len(title & _words(text))
        return ws if hits >= (max(1, math.ceil(len(title) / 2)) if strict else 1) else None

    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        if RE_LIST.match(low):
            return Intent("list_procedures", {})
        m = RE_STATUS.match(low)
        if m and self._named(m.group("n"), strict=False):
            return Intent("procedure_status", {"nome": m.group("n")})
        m = RE_GO_ON.match(low)
        if m:
            if m.group("n"):
                return Intent("continue_procedure", {"nome": m.group("n")}) if self._named(m.group("n"), strict=False) else None
            waiting = [ws for ws in procedure.procedures(self.base())
                       if any(s.get("status") == procedure.REVIEW for s in ws.load_state()["stages"].values())]
            return Intent("continue_procedure", {"nome": ""}) if len(waiting) == 1 else None
        m = RE_RULE.match(low)
        if m and (not m.group("n") or self._named(m.group("n"), strict=False)):
            return Intent("procedure_rule", {"nome": m.group("n") or "", "decisione": "no" if "lascia" in low else "sì"})
        m = RE_REDO.match(low)
        if m and self._named(m.group("n"), strict=False):
            return Intent("redo_procedure", {"nome": m.group("n"), "fase": m.group("f")})
        m = RE_RUN.match(low)
        if m and self._named(m.group("n"), strict="procedura" not in low):
            return Intent("run_procedure", {"nome": m.group("n")})
        return None
