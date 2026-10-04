"""Apprendimento nei momenti di riposo.

Il computer impara quando l'utente non lo usa (inattivo o schermo bloccato),
è collegato alla corrente e non è già impegnato. Il lavoro è diviso in passi
brevi: appena l'utente torna, si mette in pausa entro circa un secondo, e
riprende più tardi esattamente da dove era arrivato.

Standby: durante la sospensione il processore è fermo, quindi lì non si può
calcolare nulla. Il processo viene "congelato" dal kernel e al risveglio
riprende dallo stesso punto, senza perdere niente; il risveglio viene
riconosciuto (salto tra orologio di boot e orologio monotono) e l'apprendimento
resta in pausa, perché probabilmente l'utente è appena tornato.

    aios-learn             servizio in background (vedi data/aios-learn.service)
    aios-learn --now       esegue subito tutto il lavoro in coda, senza aspettare il riposo
    aios-learn --status    mostra cosa ha imparato e a che punto è
    aios-learn --forget    cancella frasi imparate e cronologia
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .fileindex import FileIndex, data_dir
from .privacy import private_dir

STEP_SECONDS = 0.5  # durata massima di un passo: è anche il ritardo massimo della pausa
MIN_IDLE_SECONDS = 120  # inattività minima prima di cominciare
LOCKED_GRACE_SECONDS = 20  # con lo schermo bloccato si parte quasi subito
WAKE_PAUSE_SECONDS = 90  # dopo un risveglio dallo standby l'utente è probabilmente presente
LEARN_AFTER = 2  # una frase si impara dopo averla vista portare 2 volte alla stessa azione
MAX_PHRASE_WORDS = 10


# --- Condizioni: l'utente c'è? il computer è in carica? --------------------------


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return ""


class Conditions:
    """Stato del sistema letto dalle interfacce standard (GNOME, KDE, logind, sysfs)."""

    def __init__(self, run: Callable[[list[str]], str] = _run, power_dir: Path = Path("/sys/class/power_supply")):
        self.run = run
        self.power_dir = power_dir

    def idle_seconds(self) -> float | None:
        out = self.run(["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
                        "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
                        "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"])
        m = re.search(r"uint64 (\d+)", out)
        if m:
            return int(m.group(1)) / 1000
        out = self.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.ScreenSaver",
                        "--object-path", "/org/freedesktop/ScreenSaver",
                        "--method", "org.freedesktop.ScreenSaver.GetSessionIdleTime"])
        m = re.search(r"uint32 (\d+)", out)
        if m:
            return float(m.group(1))
        session = self._session()
        if session.get("IdleHint") == "yes" and session.get("IdleSinceHintMonotonic", "0").isdigit():
            since = int(session["IdleSinceHintMonotonic"]) / 1e6
            return max(0.0, time.clock_gettime(time.CLOCK_MONOTONIC) - since)
        if session.get("IdleHint") == "no":
            return 0.0
        return None  # sconosciuto: per prudenza si considera l'utente presente

    def locked(self) -> bool:
        return self._session().get("LockedHint") == "yes"

    def _session(self) -> dict[str, str]:
        sid = os.environ.get("XDG_SESSION_ID", "auto")
        out = self.run(["loginctl", "show-session", sid, "-p", "IdleHint", "-p", "IdleSinceHintMonotonic", "-p", "LockedHint"])
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    def on_ac(self) -> bool:
        """In carica, oppure un computer fisso senza batteria."""
        has_battery = False
        try:
            supplies = list(self.power_dir.iterdir())
        except OSError:
            return True
        for supply in supplies:
            try:
                kind = (supply / "type").read_text().strip()
                if kind in ("Mains", "USB") and (supply / "online").read_text().strip() == "1":
                    return True
                if kind == "Battery":
                    has_battery = True
            except OSError:
                continue
        return not has_battery

    def busy(self) -> bool:
        try:
            return os.getloadavg()[0] > (os.cpu_count() or 1) * 0.7
        except OSError:
            return False


# --- Compiti --------------------------------------------------------------------


class Task(Protocol):
    name: str

    def available(self) -> bool: ...

    def has_work(self) -> bool: ...

    def step(self, seconds: float) -> None: ...


@dataclass
class IndexTask:
    index: FileIndex
    name: str = "indice dei file"

    def available(self) -> bool:
        return True

    def has_work(self) -> bool:
        return self.index.has_work()

    def step(self, seconds: float) -> None:
        self.index.step(self.index.clock() + seconds)


@dataclass
class EmbedTask:
    """Calcola il significato (embedding) dei pezzi di documento: è il lavoro pesante."""

    index: FileIndex
    embed: Callable[[Sequence[str]], list[list[float]]]
    batch: int = 4
    name: str = "significato dei documenti"
    _retry_at: float = 0.0

    def available(self) -> bool:
        return time.monotonic() >= self._retry_at

    def has_work(self) -> bool:
        return bool(self.index.chunks_without_vectors(1))

    def step(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            todo = self.index.chunks_without_vectors(self.batch)
            if not todo:
                return
            try:
                vectors = self.embed([text for _, text in todo])
            except Exception:
                self._retry_at = time.monotonic() + 600  # modello non raggiungibile: si riprova più tardi
                return
            self.index.store_vectors([(cid, v) for (cid, _), v in zip(todo, vectors)])


class History:
    """Richieste risolte dall'LLM con una sola azione riuscita (file locale, solo per l'utente)."""

    def __init__(self, path: Path | None = None):
        self.path = path or private_dir(data_dir()) / "history.jsonl"

    def record(self, text: str, tool: str, args: dict[str, Any]) -> None:
        if len(text) > 200:
            return
        new = not self.path.exists()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "text": text, "tool": tool, "args": args}, ensure_ascii=False) + "\n")
        if new:
            os.chmod(self.path, 0o600)


def learned_path() -> Path:
    return data_dir() / "learned.json"


def load_learned(path: Path | None = None) -> dict[str, list[str]]:
    try:
        data = json.loads((path or learned_path()).read_text())
        return {k: [p for p in v if isinstance(p, str)] for k, v in data.items() if isinstance(v, list)}
    except (OSError, ValueError, AttributeError):
        return {}


@dataclass
class PhraseTask:
    """Impara le frasi dell'utente: la volta dopo il livello 1 le capisce all'istante.

    Prudenza: una frase si impara solo se ha portato almeno LEARN_AFTER volte alla
    stessa azione del catalogo, mai ad azioni diverse, ed è breve e senza negazioni.
    """

    history: History
    state_path: Path
    learned_file: Path
    catalog: Sequence[Any] = ()
    name: str = "le tue frasi"
    _state: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.catalog:
            from .semantic import CATALOG

            self.catalog = CATALOG
        try:
            self._state = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            self._state = {"offset": 0, "seen": {}}

    def available(self) -> bool:
        return True

    def has_work(self) -> bool:
        try:
            return self.history.path.stat().st_size > self._state["offset"]
        except OSError:
            return False

    def step(self, seconds: float) -> None:
        from .semantic import negated, tokens

        end = time.monotonic() + seconds
        with open(self.history.path, "rb") as f:
            f.seek(self._state["offset"])
            while time.monotonic() < end:
                line = f.readline()
                if not line.endswith(b"\n"):
                    break  # riga incompleta (scrittura in corso): la si rilegge dopo
                self._state["offset"] = f.tell()
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                self._consider(entry, negated, tokens)
                self._save_state()  # checkpoint dopo ogni frase

    def _consider(self, entry: dict[str, Any], negated, tokens) -> None:
        text = str(entry.get("text", "")).strip()
        words = tokens(text)
        if not words or len(words) > MAX_PHRASE_WORDS or negated(text):
            return
        from .fastpath import Intent

        target = Intent(entry.get("tool", ""), entry.get("args") or {})
        spec = next((s for s in self.catalog if s.build() == target), None)
        key = " ".join(words)
        seen = self._state["seen"].setdefault(key, {})
        seen[spec.name if spec else "?"] = seen.get(spec.name if spec else "?", 0) + 1
        if spec is None or len(seen) > 1 or seen[spec.name] < LEARN_AFTER:
            return
        learned = load_learned(self.learned_file)
        if text not in learned.get(spec.name, []) and text not in spec.examples:
            learned.setdefault(spec.name, []).append(text)
            self.learned_file.parent.mkdir(parents=True, exist_ok=True)
            self.learned_file.write_text(json.dumps(learned, ensure_ascii=False, indent=1))

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False))
        tmp.replace(self.state_path)  # scrittura atomica


@dataclass
class DeadlineTask:
    """Cerca scadenze e appuntamenti nei documenti indicizzati: diventano *proposte*
    nel riepilogo del mattino, mai voci in agenda senza il sì dell'utente."""

    index: FileIndex
    agenda: Any
    state_path: Path
    batch: int = 40
    name: str = "scadenze nei documenti"

    def _last(self) -> int:
        try:
            return int(json.loads(self.state_path.read_text()).get("last_chunk", 0))
        except (OSError, ValueError, AttributeError):
            return 0

    def available(self) -> bool:
        return True

    def has_work(self) -> bool:
        return self.index.db.execute("SELECT 1 FROM chunks WHERE id > ? LIMIT 1", (self._last(),)).fetchone() is not None

    def step(self, seconds: float) -> None:
        from .agenda import find_deadlines

        end = time.monotonic() + seconds
        last = self._last()
        while time.monotonic() < end:
            rows = self.index.db.execute(
                "SELECT id, path, text FROM chunks WHERE id > ? ORDER BY id LIMIT ?", (last, self.batch)).fetchall()
            if not rows:
                break
            now = self.agenda.now()
            for cid, path, text in rows:
                for title, due in find_deadlines(text, now):
                    self.agenda.suggest(title, due, path)
                last = cid
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"last_chunk": last}))


@dataclass
class CatalogTask:
    """Aggiorna una volta al giorno i cataloghi generici (film, serie, app, giochi)."""

    catalog: Any
    refresh: Callable[[Any], str]
    name: str = "catalogo dei consigli"
    _retry_at: float = 0.0

    def available(self) -> bool:
        return time.monotonic() >= self._retry_at

    def has_work(self) -> bool:
        return self.catalog.stale()

    def step(self, seconds: float) -> None:
        try:
            self.refresh(self.catalog)
        except Exception:
            pass
        if self.catalog.stale():  # rete assente o catalogo irraggiungibile: si riprova fra un'ora
            self._retry_at = time.monotonic() + 3600


def activate_model(name: str, capability: str, calibrate: Callable[[str], Any] | None = None) -> None:
    """Collega un modello appena scaricato alla sua funzione nel sistema (il precedente resta per tornare indietro)."""
    from .models import load_config, save_config

    config = load_config()
    if config.get(capability) and config[capability] != name:
        config[f"_precedente_{capability}"] = config[capability]
    config[capability] = name
    save_config(config)
    if capability == "significato":  # il riconoscimento in tutte le lingue va calibrato sul modello
        try:
            (calibrate or _calibrate_embedding)(name)
        except Exception:
            pass  # resta attivo il classificatore integrato; si potrà rifare con aios-copilot-setup


def _calibrate_embedding(name: str) -> None:
    from .multilingual import calibrate, catalog_with_translations, neural_router, NeuralConfig, save_config, without_eval_phrases

    from .semantic import prefixes_for

    prefix = prefixes_for(name)[0]
    catalog = without_eval_phrases(catalog_with_translations())
    outcome = calibrate(lambda: neural_router(NeuralConfig(name, 1.0, 0.0, prefix), catalog), name, prefix)
    if outcome is not None:
        save_config(outcome[0])


def restore_model(capability: str) -> str:
    """Torna al modello usato prima per una capacità."""
    from .models import load_config, save_config

    config = load_config()
    previous = config.get(f"_precedente_{capability}")
    if not previous:
        return f"Non c'è un modello precedente per «{capability}»."
    config[f"_precedente_{capability}"], config[capability] = config.get(capability, ""), previous
    save_config(config)
    experts = json.loads(config.get("_esperti", "{}"))
    if capability == "testo" and config[f"_precedente_{capability}"] in experts and previous not in experts:
        _stop_experts_server()  # il modello a esperti non serve più: si libera la memoria
    return f"Fatto: per «{capability}» uso di nuovo {previous}."


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _trial_text_model(name: str) -> tuple[bool, str]:
    """Prova il nuovo modello di testo e lo confronta con quello attuale (punteggi in cache)."""
    from .models import load_config, save_config
    from .trial import Result, decide, run_trial

    placed = _place_experts(name)
    config = load_config()
    scores = json.loads(config.get("_punteggi", "{}"))
    new = run_trial(name, placed["call"]) if placed else run_trial(name)
    scores[name] = {"quality": new.quality, "speed": new.speed}
    current = config.get("testo")
    old = None
    if current and current != name:
        if current not in scores:
            try:
                r = run_trial(current)
                scores[current] = {"quality": r.quality, "speed": r.speed}
            except Exception:
                pass
        if current in scores:
            old = Result(current, scores[current]["quality"], scores[current]["speed"], [])
    config["_punteggi"] = json.dumps(scores)
    adopt, why = decide(new, old)
    experts = json.loads(config.get("_esperti", "{}"))
    if placed and adopt:
        experts[name] = {"modo": placed["mode"], "url": placed["url"]}
        why += f"; {placed['mode']}: {placed['how']}"
    elif placed:
        _stop_experts_server()
    config["_esperti"] = json.dumps(experts)
    save_config(config)
    return adopt, why


def _place_experts(name: str) -> dict | None:
    """Un modello a esperti che non sta tutto in GPU o in RAM: lo si avvia con llama.cpp a pezzi."""
    from .hardware import detect
    from .models import find_model
    from . import moe

    model = find_model(name)
    if model is None or not model.active_gb:
        return None
    placement = moe.best_placement(detect(), model)
    if placement is None or not placement.needs_server:
        return None  # sta tutto in GPU o in RAM: basta Ollama
    url = moe.start_server(name, placement)
    time.sleep(5)  # caricamento iniziale (con mmap è rapido: i pezzi arrivano quando servono)
    return {"mode": placement.mode, "how": placement.describe(), "url": url, "call": moe.server_call(url)}


def _stop_experts_server() -> None:
    from .tools.base import Runner

    Runner().run(["systemctl", "--user", "disable", "--now", "aios-esperti.service"])


@dataclass
class DownloadTask:
    """Scarica i modelli in coda a passi brevi (pausa e ripresa), poi li attiva."""

    queue: Any
    pull: Callable[[str, float], tuple[bool, int, int]] | None = None
    fetch: Callable[[Any, Path, float], tuple[bool, int, int]] | None = None
    activate: Callable[[str, str], None] = activate_model
    trial: Callable[[str], tuple[bool, str]] = _trial_text_model
    discard: Callable[[str], None] | None = None
    device: Callable[[], Any] | None = None  # per scegliere la variante successiva dopo uno scarto
    name: str = "scaricamento dei modelli"
    _retry_at: float = 0.0

    def available(self) -> bool:
        return time.monotonic() >= self._retry_at

    def has_work(self) -> bool:
        return bool(self.queue.pending())

    def step(self, seconds: float) -> None:
        from .models import file_download_step, find_model, models_dir, ollama_pull_step

        item = self.queue.pending()[0]
        model = find_model(item.name)
        if model is None:
            item.status, item.error = "errore", "modello sconosciuto"
            self.queue.save()
            return
        item.status = "in corso"
        try:
            if model.engine == "ollama":
                done, have, total = (self.pull or ollama_pull_step)(model.name, seconds)
            else:
                target = models_dir() / model.name
                target.mkdir(parents=True, exist_ok=True)
                done, have, total = (self.fetch or (lambda m, d, s: file_download_step(m.urls, d, s)))(model, target, seconds)
        except Exception as exc:  # rete assente, server irraggiungibile: si riprova più tardi
            item.error = str(exc)[:200]
            self._retry_at = time.monotonic() + 900
            self.queue.save()
            return
        item.done_bytes, item.total_bytes, item.error = have, total, ""
        if done:
            self._finish(item, model)
        self.queue.save()

    def _finish(self, item: Any, model: Any) -> None:
        from .models import models_dir

        if model.engine == "file" and model.sha256:  # il file deve essere esattamente quello del catalogo
            files = [models_dir() / model.name / u.rsplit("/", 1)[-1] for u in model.urls]
            if [_sha256(f) for f in files] != list(model.sha256):
                for f in files:
                    f.unlink(missing_ok=True)
                item.status, item.error = "errore", "impronta del file non corrispondente: scartato"
                return
        if model.capability == "testo":
            try:
                adopt, why = self.trial(model.name)
            except Exception as exc:
                adopt, why = False, f"prova non riuscita: {exc}"
            if not adopt:
                item.status, item.error = "scartato", why
                if self.discard is not None:
                    self.discard(model.name)
                else:
                    from .trial import delete_model

                    delete_model(model.name)
                self._try_next(item, model)
                return
            item.error = f"adottato: {why}"
        item.status = "fatto"
        self.activate(model.name, model.capability)


    def _try_next(self, item: Any, model: Any) -> None:
        """Catena di prove: se il modello non va bene qui, si prova la variante successiva
        (es. lo stesso modello più compresso, quindi più leggero e veloce)."""
        from .hardware import detect
        from .models import load_config, next_candidate, save_config

        config = load_config()
        rejected = json.loads(config.get("_scartati", "[]"))
        if model.name not in rejected:
            rejected.append(model.name)
        config["_scartati"] = json.dumps(rejected)
        save_config(config)
        try:
            following = next_candidate((self.device or detect)(), model.capability, config.get(model.capability, ""))
        except Exception:
            following = None
        if following is not None and self.queue.add(following):
            item.error += f"; provo {following.name}"


@dataclass
class MeaningModelTask:
    """Il modello del significato incluso in AIOS (EmbeddingGemma) si attiva da solo, a riposo, la prima volta:
    calibra il riconoscimento in tutte le lingue e da lì in poi i documenti si cercano anche per significato."""

    model: str = "embeddinggemma"
    installed: Callable[[], set[str]] | None = None
    activate: Callable[[str, str], None] | None = None
    name: str = "modello del significato"
    _retry_at: float = 0.0

    def available(self) -> bool:
        return time.monotonic() >= self._retry_at

    def has_work(self) -> bool:
        from .models import load_config

        if load_config().get("significato"):
            return False  # già scelto (dall'utente o prima): non si tocca
        try:
            from .multilingual import installed_models

            return self.model in (self.installed or installed_models)()
        except Exception:
            self._retry_at = time.monotonic() + 3600
            return False

    def step(self, seconds: float) -> None:
        try:
            (self.activate or activate_model)(self.model, "significato")
        except Exception:
            self._retry_at = time.monotonic() + 3600


@dataclass
class ModelCatalogTask:
    """Controlla una volta a settimana se c'è un catalogo dei modelli più recente."""

    update: Callable[[], str] | None = None
    name: str = "catalogo dei modelli"
    _retry_at: float = 0.0

    def available(self) -> bool:
        return time.monotonic() >= self._retry_at

    def has_work(self) -> bool:
        from .models import load_config

        return time.time() - float(load_config().get("_catalogo_controllato", 0)) > 7 * 86400

    def step(self, seconds: float) -> None:
        from .models import load_config, save_config

        try:
            from .modelcatalog import update

            (self.update or update)()
        except Exception:
            self._retry_at = time.monotonic() + 3600  # rete assente: si riprova più tardi
            return
        config = load_config()
        config["_catalogo_controllato"] = str(time.time())
        save_config(config)


@dataclass
class OrganizeTask:
    """Aggiorna le raccolte automatiche (e la cartella ~/Raccolte) ogni mezz'ora di riposo."""

    library: Any
    view: bool = True
    name: str = "raccolte dei tuoi file"
    _last: float | None = None

    def available(self) -> bool:
        return True

    def has_work(self) -> bool:
        return self._last is None or time.monotonic() - self._last > 1800

    def step(self, seconds: float) -> None:
        if not self.library.refresh(deadline=time.monotonic() + seconds):
            return  # riprende al passo successivo
        self._last = time.monotonic()
        if self.view:
            try:
                self.library.build_view()
            except (OSError, RuntimeError):
                pass  # es. esiste già una cartella «Raccolte» dell'utente: non la si tocca


# --- Pianificatore ------------------------------------------------------------------


def lower_priority() -> None:
    """La CPU va all'apprendimento solo quando nessun altro la usa."""
    try:
        os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
    except (AttributeError, OSError):
        try:
            os.nice(19)
        except OSError:
            pass
    _run(["ionice", "-c", "3", "-p", str(os.getpid())])


def suspend_offset() -> float:
    """Tempo trascorso in standby dall'avvio (l'orologio di boot conta lo standby, quello monotono no)."""
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC)


@dataclass
class Scheduler:
    tasks: Sequence[Task]
    conditions: Conditions = field(default_factory=Conditions)
    clock: Callable[[], float] = time.monotonic
    suspended: Callable[[], float] = suspend_offset
    sleep: Callable[[float], None] = time.sleep
    on_status: Callable[[dict[str, Any]], None] | None = None
    _paused_until: float = 0.0
    _last_offset: float | None = None

    def check_wake(self) -> bool:
        offset = self.suspended()
        woke = self._last_offset is not None and offset - self._last_offset > 2.0
        self._last_offset = offset
        if woke:
            self._paused_until = self.clock() + WAKE_PAUSE_SECONDS
        return woke

    def can_run(self) -> tuple[bool, str]:
        if self.clock() < self._paused_until:
            return False, "appena risvegliato dallo standby"
        if not self.conditions.on_ac():
            return False, "a batteria"
        if self.conditions.busy():
            return False, "computer già impegnato"
        if self.conditions.locked():
            idle = self.conditions.idle_seconds()
            if idle is None or idle >= LOCKED_GRACE_SECONDS:
                return True, "schermo bloccato"
        idle = self.conditions.idle_seconds()
        if idle is None:
            return False, "attività dell'utente sconosciuta"
        if idle < MIN_IDLE_SECONDS:
            return False, "utente presente"
        return True, f"inattivo da {int(idle)} s"

    def next_task(self) -> Task | None:
        return next((t for t in self.tasks if t.available() and t.has_work()), None)

    def tick(self, force: bool = False) -> str:
        """Un giro: un passo di lavoro, oppure il motivo per cui si aspetta."""
        if self.check_wake() and not force:
            return self._status("pausa", "appena risvegliato dallo standby")
        ok, reason = (True, "su richiesta") if force else self.can_run()
        if not ok:
            return self._status("pausa", reason)
        task = self.next_task()
        if task is None:
            return self._status("fatto", "niente da imparare per ora")
        task.step(STEP_SECONDS)
        return self._status("al lavoro", reason, task.name)

    def _status(self, state: str, reason: str, task: str = "") -> str:
        if self.on_status:
            self.on_status({"state": state, "reason": reason, "task": task, "time": time.time()})
        return state

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            state = self.tick()
            if state == "pausa":
                self.sleep(5)
            elif state == "fatto":
                self.sleep(60)
            # "al lavoro": subito il passo successivo, ricontrollando l'utente


# --- Comando ------------------------------------------------------------------------


def build(index: FileIndex | None = None) -> tuple[Scheduler, FileIndex]:
    from .multilingual import load_config
    from .semantic import OllamaEncoder

    index = index or FileIndex()
    tasks: list[Task] = [
        PhraseTask(History(), data_dir() / "learn-state.json", learned_path()),  # leggero: per primo
        IndexTask(index),
    ]
    from .agenda import Agenda

    tasks.append(DeadlineTask(index, Agenda(), data_dir() / "deadline-state.json"))
    from .recommend import Catalog, refresh_catalog

    tasks.append(CatalogTask(Catalog(), refresh_catalog))
    from .models import Queue

    tasks.insert(0, DownloadTask(Queue()))  # un modello richiesto dall'utente ha la precedenza
    tasks.append(ModelCatalogTask())
    tasks.append(MeaningModelTask())  # EmbeddingGemma, incluso nell'immagine
    from .organize import Library

    tasks.append(OrganizeTask(Library()))
    from .galleria import GalleryTask

    tasks.append(GalleryTask())  # foto: cosa c'è, scritte, persone (solo se l'utente l'ha acceso)
    from .agenda import notify
    from .updates import UpdateTask

    tasks.append(UpdateTask(notify=notify))
    config = load_config()
    if config is not None:
        from .semantic import prefixes_for

        index.vectors_for(config.model)
        encoder = OllamaEncoder(config.model, prefix=prefixes_for(config.model)[2])  # i documenti
        tasks.append(EmbedTask(index, encoder._embed))
    status_path = data_dir() / "learn-status.json"

    def write_status(status: dict[str, Any]) -> None:
        try:
            status_path.write_text(json.dumps({**status, **index.stats()}, ensure_ascii=False))
        except OSError:
            pass

    return Scheduler(tasks, on_status=write_status), index


def _print_status() -> int:
    try:
        status = json.loads((data_dir() / "learn-status.json").read_text())
    except (OSError, ValueError):
        status = {}
    learned = load_learned()
    print(f"Stato: {status.get('state', 'mai avviato')} ({status.get('reason', '-')})")
    if status:
        print(f"Indice: {status.get('files', 0)} file, {status.get('chunks', 0)} parti, "
              f"{status.get('with_vectors', 0)} con significato, {status.get('pending', 0)} in coda")
    print(f"Frasi imparate: {sum(len(v) for v in learned.values())}")
    for intent, phrases in learned.items():
        print(f"  {intent}: {', '.join(phrases)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aios-learn", description="Apprendimento di AIOS nei momenti di riposo")
    parser.add_argument("--now", action="store_true", help="esegue subito il lavoro in coda")
    parser.add_argument("--status", action="store_true", help="mostra lo stato")
    parser.add_argument("--forget", action="store_true", help="cancella frasi imparate e cronologia")
    args = parser.parse_args(argv)

    if args.status:
        return _print_status()
    if args.forget:
        for path in (learned_path(), data_dir() / "history.jsonl", data_dir() / "learn-state.json"):
            path.unlink(missing_ok=True)
        print("Frasi imparate e cronologia cancellate.")
        return 0

    lower_priority()
    scheduler, index = build()
    if args.now:
        start = time.monotonic()
        while scheduler.tick(force=True) == "al lavoro":
            stats = index.stats()
            print(f"\r  {stats['files']} file, {stats['chunks']} parti, {stats['with_vectors']} con significato, "
                  f"{stats['pending']} in coda", end="", flush=True)
        print(f"\nFatto in {time.monotonic() - start:.0f} s.")
        return 0
    try:
        scheduler.run_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
