"""L'agente programmatore: Nova che modifica il codice di SoIA su richiesta dell'utente («voglio l'orologio rotondo»).

Lavora sulla copia personale del codice (codice.py) come farebbe un programmatore: cerca dove si fa la cosa,
legge i file, li modifica con cambi precisi, controlla che il codice regga e salva la modifica nella storia con
la richiesta come descrizione. Può toccare solo i file della copia; codice.py e __init__.py (il meccanismo che
sceglie quale codice usare e lo protegge) restano fuori.

Le modifiche che aggiungono accessi a internet, comandi di sistema o cancellazioni non si applicano da sole:
restano in attesa finché l'utente non dice di sì.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import codice

MAX_STEPS = 40
PROTECTED = {"aios_copilot/codice.py", "aios_copilot/__init__.py", "aios_copilot/programmatore.py", "aios_copilot/anteprima.py",
             "aios_copilot/cambiamenti.py"}
MAX_LOOKS = 6
PAGE_FILES = (".html", ".js", ".css")
RISKY = re.compile(r"\b(?:urllib|requests\.|http\.client|socket\.|subprocess|os\.system|os\.remove|os\.unlink|shutil\.rmtree|"
                   r"\.unlink\(|rmtree|eval\(|exec\(|fetch\(\s*[\"']https?:|XMLHttpRequest|WebSocket\()")

MAP = """Mappa del codice di SoIA (Python + pagine HTML/JS/CSS nella shell, mostrate da WebKitGTK):
- aios_copilot/shell/home.html: la schermata principale (barra in alto con finestre aperte, orologio, campanella; la giornata a
  sinistra; Nova al centro; i widget a destra: render in JS di meteo, mappa, orologio, nota; il dock in basso). CSS in testa al file.
- aios_copilot/shell/static/apps.js e apps.css: le app di SoIA dentro la shell (File, Foto, Musica, Video, Note, Calendario,
  Rubrica, Gestione attività, Impostazioni con tutte le sezioni). icone.js: le icone (tracciati SVG) e i colori delle piastrelle.
- aios_copilot/shell/static/pannello.html: il pannello sopra i programmi (appunti, emoji, notifiche).
- aios_copilot/shell/__init__.py e shell/apps.py: il server della shell (rotte /api/...) e la finestra GTK.
- aios_copilot/widget.py: i dati dei widget (TIPI); tools/*.py: gli strumenti di Nova; agent.py: Nova.
- Colori e stile: variabili CSS in :root di home.html (--turchese, --blu, --carta, --testo, --tenue…), tema chiaro e scuro
  (prefers-color-scheme). I testi per l'utente sono in italiano."""

SYSTEM = """Sei il programmatore di SoIA: modifichi il codice del sistema operativo dell'utente per fare quello che chiede.
{map}

Come lavori:
1. Trova dove si fa la cosa: usa cerca e elenca, poi leggi solo le parti che servono (leggi con da/a).
2. Fai il cambiamento più piccolo e pulito che soddisfa la richiesta, nello stile del codice intorno (stessi nomi, commenti brevi
   in italiano). Non toccare altro. Funziona sia col tema chiaro sia con quello scuro.
   Non togliere niente di quello che c'era (dati mostrati, testi, opzioni come il fuso orario di un orologio): aggiungi o
   trasforma, a meno che l'utente chieda di togliere.
3. Modifica con modifica_file (testo vecchio esatto e unico → testo nuovo); scrivi_file solo per file nuovi.
4. Rileggi quello che hai scritto e verifica la logica a mente: calcoli, unità, angoli (un orologio: ore × 30°, minuti × 6°),
   centri e punti di rotazione, che cosa succede col tema scuro. Poi chiama controlla; se segnala errori, correggi e ricontrolla.
5. Se hai cambiato una pagina (HTML, JS, CSS) chiama guarda sulla pagina giusta («casa» per la schermata principale,
   «impostazioni/aspetto» o «attivita» per le app) e chiedi di controllare proprio quello che hai cambiato (es. «le lancette
   segnano l'ora giusta?»). Ti dice gli errori di JavaScript, i testi tagliati o sovrapposti e cosa vede: se qualcosa non va,
   correggi e riguarda.
6. Quando hai finito chiama fatto con un riassunto in italiano per l'utente (cosa hai cambiato, dove, come annullarlo).
Non inventare file o funzioni: verifica sempre leggendo. Non puoi toccare codice.py, __init__.py e programmatore.py.
Niente accessi a internet, comandi di sistema o cancellazioni di file dell'utente, a meno che la richiesta lo richieda davvero."""


def _param(**props: str) -> dict[str, Any]:
    return {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in props.items()},
            "required": list(props)}


TOOLS = [
    {"type": "function", "function": {"name": "elenca", "description": "Elenca file e cartelle di una cartella del codice.",
                                      "parameters": _param(cartella="Percorso relativo, es. aios_copilot/shell")}},
    {"type": "function", "function": {"name": "cerca", "description": "Cerca un testo (o un'espressione regolare) nei file del codice.",
                                      "parameters": _param(testo="Cosa cercare", cartella="Dove (es. aios_copilot o aios_copilot/shell)")}},
    {"type": "function", "function": {"name": "leggi", "description": "Legge un file con i numeri di riga (al massimo 250 righe alla volta).",
                                      "parameters": _param(percorso="File", da="Prima riga (1)", a="Ultima riga")}},
    {"type": "function", "function": {"name": "modifica_file", "description": "Sostituisce un pezzo di testo esatto (deve comparire una volta sola).",
                                      "parameters": _param(percorso="File", vecchio="Testo esatto da sostituire", nuovo="Testo nuovo")}},
    {"type": "function", "function": {"name": "scrivi_file", "description": "Crea un file nuovo.",
                                      "parameters": _param(percorso="File nuovo", contenuto="Contenuto")}},
    {"type": "function", "function": {"name": "controlla", "description": "Controlla che il codice modificato regga (sintassi, parentesi, moduli).",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "guarda", "description": "Disegna una pagina di SoIA col codice modificato e la "
                                      "controlla: errori di JavaScript, testi tagliati o sovrapposti, e cosa si vede nella foto.",
                                      "parameters": _param(pagina="casa, impostazioni/<sezione>, attivita, calendario, pannello/emoji…",
                                                           domanda="Cosa controllare in particolare")}},
    {"type": "function", "function": {"name": "fatto", "description": "Fine del lavoro: riassunto per l'utente.",
                                      "parameters": _param(riassunto="Cosa hai cambiato, in italiano semplice")}},
]


class Workspace:
    """I file della copia personale, e niente fuori."""

    def __init__(self, base: Path | None = None):
        self.base = (base or codice.root()).resolve()

    def path(self, rel: str) -> Path:
        rel = rel.strip().lstrip("/")
        p = (self.base / rel).resolve()
        p.relative_to(self.base)  # ValueError se esce dalla copia
        if p.relative_to(self.base).parts[:1] == (".git",):
            raise ValueError("la cartella .git non si tocca")
        return p

    def list(self, folder: str) -> str:
        p = self.path(folder or "aios_copilot")
        if not p.is_dir():
            return f"«{folder}» non è una cartella."
        items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        return "\n".join(f"{x.name}/" if x.is_dir() else f"{x.name} ({x.stat().st_size} byte)"
                         for x in items if x.name not in ("__pycache__",) and not x.name.startswith("."))[:4000]

    def search(self, text: str, folder: str = "aios_copilot") -> str:
        p = self.path(folder or "aios_copilot")
        try:
            out = subprocess.run(["grep", "-rnIE", "--exclude-dir=__pycache__", "-m", "15", text, str(p)],
                                 capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            return f"Ricerca non riuscita: {exc}"
        lines = [l.replace(str(self.base) + "/", "") for l in out.splitlines()]
        lines = [l[:220] for l in lines][:60]
        return "\n".join(lines) or "Nessun risultato."

    def read(self, rel: str, start: str = "1", end: str = "") -> str:
        p = self.path(rel)
        if not p.is_file():
            return f"Il file «{rel}» non esiste."
        lines = p.read_text(errors="replace").splitlines()
        a = max(1, int(start or 1))
        b = min(len(lines), int(end) if end else a + 249, a + 249)
        body = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(a, b + 1))
        return body + (f"\n… (il file ha {len(lines)} righe)" if b < len(lines) else "")

    def edit(self, rel: str, old: str, new: str) -> str:
        if rel.strip().lstrip("/") in PROTECTED:
            return f"«{rel}» è protetto: non si modifica."
        p = self.path(rel)
        if not p.is_file():
            return f"Il file «{rel}» non esiste."
        text = p.read_text()
        n = text.count(old)
        if not old or n == 0:
            return "Il testo vecchio non c'è nel file: rileggi la parte e copia esattamente (spazi compresi)."
        if n > 1:
            return f"Il testo vecchio compare {n} volte: allargalo con qualche riga intorno perché sia unico."
        p.write_text(text.replace(old, new, 1))
        return "Modificato."

    def write(self, rel: str, content: str) -> str:
        if rel.strip().lstrip("/") in PROTECTED:
            return f"«{rel}» è protetto."
        p = self.path(rel)
        if p.exists():
            return "Il file esiste già: usa modifica_file."
        if not str(p.relative_to(self.base)).startswith("aios_copilot/"):
            return "I file nuovi vanno dentro aios_copilot/."
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return "Creato."


def risky_lines(diff_text: str) -> list[str]:
    return [l[1:].strip()[:160] for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++") and RISKY.search(l)]


def pick_model() -> tuple[Any, str]:
    """Per scrivere codice serve un modello forte: quello in cloud se l'utente l'ha acceso, altrimenti quello del PC."""
    try:
        from .cloud import CloudModel, Escalation

        if Escalation().available():
            m = CloudModel()
            return m, m.label
    except Exception:
        pass
    from .llm import make_client

    m = make_client(slot=1)  # il posto dei lavori lunghi: la conversazione con Nova resta pronta
    return m, getattr(m, "model", "locale")


class Programmer:
    def __init__(self, model: Any = None, workspace: Workspace | None = None,
                 check: Callable[[], tuple[bool, str]] | None = None, on_step: Callable[[str], None] = lambda s: None,
                 eyes: Any = None):
        self.model = model
        self.ws = workspace or Workspace()
        self.check = check or (lambda: codice.check())
        self.on_step = on_step
        self.eyes = eyes  # anteprima.Eyes: il controllo visivo (None = niente)
        self.checked_ok = False
        self.page_changed = False  # una pagina cambiata e non ancora guardata
        self.page_broken = False  # l'ultima occhiata ha trovato errori di JavaScript o problemi nuovi
        self.looks = 0

    def _must_look(self) -> str:
        """Prima di finire: la pagina cambiata va guardata (se si può), e senza errori."""
        if self.eyes is None or self.looks >= MAX_LOOKS or not self.eyes.available():
            return ""
        if self.page_changed:
            return "Hai cambiato una pagina: prima guardala con guarda e controlla che sia come chiede l'utente."
        if self.page_broken:
            return "L'ultima occhiata ha trovato errori o problemi nuovi nella pagina: correggili e riguarda."
        return ""

    def run(self, request: str) -> tuple[bool, str]:
        """→ (riuscito, riassunto o motivo)."""
        if self.model is None:
            self.model, _ = pick_model()
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM.format(map=MAP)},
                                          {"role": "user", "content": f"Richiesta dell'utente: {request}"}]
        summary = ""
        for step in range(MAX_STEPS):
            if not getattr(self.model, "is_cloud", False):  # col modello del PC: prima l'utente che parla con Nova
                from .precedenza import wait_turn

                wait_turn(limit=600)
            reply = self.model.chat(messages, TOOLS)
            calls = reply.get("tool_calls") or []
            messages.append({"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls})
            if not calls:
                if self.checked_ok and not self._must_look() and (reply.get("content") or "").strip():
                    return True, reply["content"].strip()
                messages.append({"role": "user", "content": "Usa gli strumenti: trova il codice, modificalo, chiama controlla e poi fatto."})
                continue
            for call in calls:
                name = call.get("function", {}).get("name", "")
                args = call.get("function", {}).get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                result = self._call(name, args)
                self.on_step(f"{name} {args.get('percorso') or args.get('pagina') or args.get('testo') or args.get('cartella') or ''}".strip())
                if name == "fatto":
                    if not self.checked_ok:
                        result = "Prima chiama controlla e assicurati che vada tutto bene."
                    elif self._must_look():
                        result = self._must_look()
                    else:
                        summary = str(args.get("riassunto", "")).strip() or "Modifica fatta."
                        return True, summary
                messages.append({"role": "tool", "tool_name": name, "content": result})
        return False, "Non sono riuscita a finire la modifica in tempo."

    def _call(self, name: str, a: dict[str, Any]) -> str:
        try:
            if name == "elenca":
                return self.ws.list(str(a.get("cartella", "")))
            if name == "cerca":
                return self.ws.search(str(a.get("testo", "")), str(a.get("cartella", "aios_copilot")))
            if name == "leggi":
                return self.ws.read(str(a.get("percorso", "")), str(a.get("da", "1")), str(a.get("a", "")))
            if name in ("modifica_file", "scrivi_file") and str(a.get("percorso", "")).endswith(PAGE_FILES):
                self.page_changed = True
            if name == "modifica_file":
                self.checked_ok = False
                return self.ws.edit(str(a.get("percorso", "")), str(a.get("vecchio", "")), str(a.get("nuovo", "")))
            if name == "scrivi_file":
                self.checked_ok = False
                return self.ws.write(str(a.get("percorso", "")), str(a.get("contenuto", "")))
            if name == "controlla":
                ok, msg = self.check()
                self.checked_ok = ok
                return ("Tutto a posto. " if ok else "Problema: ") + msg
            if name == "guarda":
                if self.eyes is None or not self.eyes.available():
                    return "Il controllo visivo qui non si può fare: rileggi bene la logica di quello che hai cambiato."
                self.looks += 1
                seen = self.eyes(str(a.get("pagina", "casa")) or "casa", str(a.get("domanda", "")))
                self.page_changed = False
                self.page_broken = bool(getattr(self.eyes, "broken", False))
                return seen
            if name == "fatto":
                return "ok"
        except ValueError as exc:
            return f"Percorso non consentito: {exc}"
        except Exception as exc:  # un errore dello strumento si racconta al modello, che può correggersi
            return f"Errore: {exc}"
        return f"Strumento sconosciuto: {name}"


# --- il programmatore a fasi (ICM, docs/PROCEDURE.md punto 6) ---------------------------------------------------
PLAN_SYSTEM = """Sei il programmatore di SoIA. Prima di toccare il codice fai il piano di una modifica chiesta dall'utente.
{map}

Rispondi solo con un JSON:
{{"pagina": "la pagina da guardare dopo: casa, impostazioni/<sezione>, attivita, calendario, pannello/emoji… oppure nessuna",
  "file": ["i file da cambiare, dalla mappa"],
  "cerca": ["2-5 parole o pezzi di codice da cercare per trovare il punto giusto (nomi di classi, id, testi visibili)"],
  "cosa": "cosa cambiare, in una frase",
  "controlli": ["cosa deve risultare vero dopo la modifica, guardando la pagina"]}}"""

EDIT_SYSTEM = """Sei il programmatore di SoIA: fai una modifica al codice del sistema operativo dell'utente.
Hai il piano e i pezzi di codice giusti, trovati prima di te: lavora su quelli (se serve, leggi o cerca ancora).
- Fai il cambiamento più piccolo e pulito che soddisfa la richiesta, nello stile del codice intorno. Non toccare altro.
- Non togliere niente di quello che c'era, a meno che l'utente lo chieda. Funziona col tema chiaro e con quello scuro.
- Modifica con modifica_file (testo vecchio esatto e unico → testo nuovo); scrivi_file solo per file nuovi.
- Rileggi quello che hai scritto e verifica la logica a mente (calcoli, unità, angoli, centri di rotazione).
Quando hai finito rispondi con una riga che dice cosa hai cambiato, senza chiamare strumenti."""

EDIT_TOOLS = [t for t in TOOLS if t["function"]["name"] in ("cerca", "leggi", "modifica_file", "scrivi_file")]
MAX_EDIT_STEPS = 20
MAX_ROUNDS = 3  # modifica → verifica: al massimo tre giri
EXCERPT_CHARS = 14000


def work_dir() -> Path:
    """Dove restano le fasi dell'ultima personalizzazione (piano, codice trovato, verifica): si possono leggere."""
    return codice.state_path().parent / "lavoro"


class StagedProgrammer(Programmer):
    """Il programmatore a fasi, come una procedura: piano (modello, niente strumenti) → codice (lo trova il codice)
    → modifica (modello, solo il piano e i pezzi trovati) → verifica (il codice e il controllo visivo; se non va,
    di nuovo modifica con il problema scritto) → riassunto. Ogni fase vede solo quello che le serve: un modello
    piccolo del PC non si perde in un contesto lungo. Le fasi restano scritte in work_dir()."""

    def run(self, request: str) -> tuple[bool, str]:
        if self.model is None:
            self.model, _ = pick_model()
        folder = work_dir()
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.md"):
            old.unlink()
        plan = self.plan(request)
        (folder / "01_piano.md").write_text(json.dumps(plan, ensure_ascii=False, indent=1))
        excerpts = self.find_code(plan)
        (folder / "02_codice.md").write_text(excerpts)
        self.on_step("piano: " + plan["cosa"])
        messages = [{"role": "system", "content": EDIT_SYSTEM},
                    {"role": "user", "content": f"Richiesta dell'utente: {request}\n\nIl piano:\n- cosa: {plan['cosa']}\n"
                                                f"- file: {', '.join(plan['file']) or 'da trovare'}\n"
                                                f"- dopo deve risultare: {'; '.join(plan['controlli']) or request}\n\n"
                                                f"Il codice che serve:\n{excerpts}"}]
        report = ""
        for round_ in range(MAX_ROUNDS):
            said = self.edit(messages)
            problem, report = self.verify(plan)
            (folder / "04_verifica.md").write_text(f"Giro {round_ + 1}\n\n{report}\n")
            if not problem:
                summary = self.summary(request, plan, said, report)
                (folder / "05_riassunto.md").write_text(summary + "\n")
                return True, summary
            messages.append({"role": "user", "content": "(Controllo automatico di SoIA, non dell'utente) La modifica non va ancora: "
                                                        f"{problem}\nCorreggi e poi rispondi con una riga."})
        return False, "Non sono riuscita a finire la modifica: " + report[:300]

    def _wait(self) -> None:
        if not getattr(self.model, "is_cloud", False):  # col modello del PC: prima l'utente che parla con Nova
            from .precedenza import wait_turn

            wait_turn(limit=600)

    def plan(self, request: str) -> dict[str, Any]:
        self._wait()
        reply = self.model.chat([{"role": "system", "content": PLAN_SYSTEM.format(map=MAP)},
                                 {"role": "user", "content": f"Richiesta dell'utente: {request}"}], [])
        data: dict[str, Any] = {}
        m = re.search(r"\{.*\}", reply.get("content") or "", re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except ValueError:
                data = {}
        as_list = lambda v: [str(x).strip() for x in (v if isinstance(v, list) else [v] if v else []) if str(x).strip()]
        files = [f for f in as_list(data.get("file")) if self._exists(f)]
        page = str(data.get("pagina") or "").strip()
        return {"pagina": "" if page.lower() in ("", "nessuna", "none") else page, "file": files[:4],
                "cerca": as_list(data.get("cerca"))[:5] or [w for w in re.findall(r"\w{5,}", request)][:3],
                "cosa": str(data.get("cosa") or request).strip(), "controlli": as_list(data.get("controlli"))[:4]}

    def _exists(self, rel: str) -> bool:
        try:
            return self.ws.path(rel).is_file()
        except ValueError:
            return False

    def find_code(self, plan: dict[str, Any]) -> str:
        """I pezzi di codice giusti, trovati dal codice (grep), non dal modello: le righe intorno a ogni risultato."""
        hits: dict[str, list[int]] = {}
        for term in plan["cerca"]:
            for line in self.ws.search(re.escape(term), "aios_copilot").splitlines():
                m = re.match(r"^(.+?):(\d+):", line)
                if m:
                    hits.setdefault(m.group(1), []).append(int(m.group(2)))
        if plan["file"]:  # i file del piano prima di tutto; gli altri risultati solo se nei file del piano non c'è niente
            mine = {f: hits.get(f, []) for f in plan["file"]}
            hits = mine if any(mine.values()) else {**hits, **mine}
        parts, used = [], 0
        for rel, lines in sorted(hits.items(), key=lambda kv: (kv[0] not in plan["file"], -len(kv[1]), kv[0])):
            try:
                text = self.ws.path(rel).read_text(errors="replace").splitlines()
            except (OSError, ValueError):
                continue
            ranges: list[list[int]] = []
            for n in sorted(set(lines))[:8] or [1]:
                a, b = max(1, n - 20), min(len(text), n + 25)
                if ranges and a <= ranges[-1][1] + 5:
                    ranges[-1][1] = max(ranges[-1][1], b)
                else:
                    ranges.append([a, b])
            for a, b in ranges:
                chunk = f"### {rel}, righe {a}-{b}\n" + "\n".join(f"{i}\t{text[i - 1]}" for i in range(a, b + 1))
                if used + len(chunk) > EXCERPT_CHARS:
                    break
                parts.append(chunk)
                used += len(chunk)
        return "\n\n".join(parts) or "(non ho trovato niente: cerca tu con cerca e leggi)"

    def edit(self, messages: list[dict[str, Any]]) -> str:
        for _ in range(MAX_EDIT_STEPS):
            self._wait()
            reply = self.model.chat(messages, EDIT_TOOLS)
            calls = reply.get("tool_calls") or []
            messages.append({"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls})
            if not calls:
                if self.changed:
                    return (reply.get("content") or "").strip()
                messages.append({"role": "user", "content": "Non hai ancora cambiato niente: fai la modifica con modifica_file."})
                continue
            for call in calls:
                name = call.get("function", {}).get("name", "")
                args = call.get("function", {}).get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                result = self._call(name, args) if name in ("cerca", "leggi", "modifica_file", "scrivi_file") else \
                    "In questa fase puoi solo cercare, leggere e modificare."
                if name in ("modifica_file", "scrivi_file") and result in ("Modificato.", "Creato."):
                    self.changed = True
                self.on_step(f"{name} {args.get('percorso') or args.get('testo') or ''}".strip())
                messages.append({"role": "tool", "tool_name": name, "content": result})
        return ""

    changed = False

    def verify(self, plan: dict[str, Any]) -> tuple[str, str]:
        """→ (problema da correggere o "", resoconto). Lo fa il codice: controlla, poi il controllo visivo della pagina."""
        ok, msg = self.check()
        if not ok:
            return f"il codice non regge: {msg}", f"Controllo del codice: {msg}"
        report = "Controllo del codice: tutto a posto."
        if self.page_changed and self.eyes is not None and self.eyes.available() and self.looks < MAX_LOOKS:
            self.looks += 1
            seen = self.eyes(plan["pagina"] or "casa", "; ".join(plan["controlli"]))
            self.page_changed = False
            report += "\n\n" + seen
            if getattr(self.eyes, "broken", False):
                return "la pagina ha errori o problemi nuovi:\n" + seen, report
            if "non sembra riuscita" in seen or "uguale a prima" in seen:
                return seen, report
        return "", report

    def summary(self, request: str, plan: dict[str, Any], said: str, report: str) -> str:
        try:
            self._wait()
            reply = self.model.chat([{"role": "system", "content": "Scrivi in due o tre frasi semplici, in italiano, cosa è stato "
                                                                    "cambiato in SoIA per l'utente, e che può toglierlo da Impostazioni › "
                                                                    "Personalizzazioni. Niente codice."},
                                     {"role": "user", "content": f"Richiesta: {request}\nCosa è stato fatto: {said or plan['cosa']}\n"
                                                                 f"Verifica:\n{report[:1500]}"}], [])
            text = (reply.get("content") or "").strip()
        except Exception:
            text = ""
        return text or f"Fatto: {plan['cosa']}. Puoi toglierlo da Impostazioni › Personalizzazioni."


def choose_programmer(model: Any = None) -> tuple[type, Any]:
    """Quale programmatore: a fasi col modello del PC (scelto da SoIA), il ciclo libero col cloud o con un modello
    passato da chi chiama. AIOS_PROGRAMMATORE=fasi|libero lo decide a mano."""
    import os

    mode = os.environ.get("AIOS_PROGRAMMATORE", "")
    if model is None and mode != "libero":
        model, _ = pick_model()
        if mode == "fasi" or not getattr(model, "is_cloud", False):
            return StagedProgrammer, model
        return Programmer, model
    return (StagedProgrammer if mode == "fasi" else Programmer), model


def customize(request: str, model: Any = None, on_step: Callable[[str], None] = lambda s: None,
              confirm_risky: bool = False, retouch: str = "") -> dict[str, Any]:
    """Una richiesta dell'utente → una modifica salvata nella copia personale (o niente, se non riesce).
    Con «retouch» (la matitina) si ritocca una personalizzazione già fatta."""
    codice.ensure()
    codice.discard()  # si parte puliti
    target = codice.find(retouch) if retouch else None
    if retouch and target is None:
        return {"ok": False, "messaggio": "Non trovo la personalizzazione da ritoccare."}
    task = request
    if target is not None:
        task = (f"Ritocca una personalizzazione già applicata: «{target['richiesta']}» ({target['dettagli'][:300]}).\n"
                f"Ecco cosa cambiava nel codice:\n{codice.show(target)}\n\nCosa vuole adesso l'utente: {request}")
    eyes = None
    try:
        from .anteprima import Eyes

        eyes = Eyes(codice.root(), request)
    except Exception:
        pass
    kind, model = choose_programmer(model)
    ok, summary = kind(model, on_step=on_step, eyes=eyes).run(task)
    if not ok:
        codice.discard()
        return {"ok": False, "messaggio": summary}
    changed = codice.changed_files()
    if not changed:
        return {"ok": False, "messaggio": "Non ho cambiato nessun file: " + summary}
    risky = risky_lines(codice.diff())
    if risky and not confirm_risky:
        codice.save_state(in_attesa={"richiesta": request, "riassunto": summary, "ritocco": target})
        return {"ok": False, "in_attesa": True, "rischi": risky[:6], "file": changed,
                "messaggio": "La modifica usa internet, comandi di sistema o cancella file: " + "; ".join(risky[:3])
                             + ". Se vuoi applicarla lo stesso, dimmi «applica la personalizzazione»."}
    cid = codice.commit(request, summary, retouch=target)
    codice.save_state(in_attesa=None)
    return {"ok": True, "id": cid, "file": changed, "messaggio": summary}


def apply_pending() -> dict[str, Any]:
    pending = codice.state().get("in_attesa")
    if not pending or not codice.changed_files():
        return {"ok": False, "messaggio": "Non c'è nessuna personalizzazione in attesa."}
    ok, msg = codice.check()
    if not ok:
        codice.discard()
        codice.save_state(in_attesa=None)
        return {"ok": False, "messaggio": "La modifica in attesa non regge più: " + msg}
    cid = codice.commit(pending["richiesta"], pending["riassunto"], retouch=pending.get("ritocco"))
    codice.save_state(in_attesa=None)
    return {"ok": True, "id": cid, "messaggio": pending["riassunto"]}
