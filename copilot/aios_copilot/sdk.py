"""SDK delle abilità: le app offrono le loro funzioni al copilota.

Un'app dichiara le sue abilità in un file JSON (manifesto) in una di queste cartelle:
/usr/share/aios/abilita, /etc/aios/abilita, ~/.local/share/aios/abilita. Ogni abilità ha:
nome, descrizione, parametri tipizzati, frasi d'esempio (riconosciute all'istante, senza
modello AI) e il modo di chiamarla:

- "esegui": un comando con segnaposto, es. ["gnome-calculator", "--solve", "{espressione}"].
  Nessuna shell: ogni valore è un argomento a sé, quindi niente iniezione di comandi;
  un valore che comincia con «-» è rifiutato (niente opzioni nascoste).
- "dbus": {servizio, percorso, interfaccia, metodo}: il metodo riceve un testo JSON con
  i parametri e restituisce un testo. Va bene per app già aperte.

Sicurezza: il risultato di un'app è trattato come dato esterno (mai come istruzioni);
le abilità che cambiano qualcosa dichiarano "conferma": true e chiedono il permesso;
quelle che leggono dati personali o li mandano fuori lo dichiarano e passano dalla
protezione dalle fughe di dati del copilota. Tempo massimo 30 s, risposta tagliata.

Per gli sviluppatori Python c'è `Abilities` (vedi docs/SDK.md):

    app = Abilities("org.esempio.Ricette", "Ricette")

    @app.ability(frasi=["cerca una ricetta con {ingrediente}"])
    def cerca_ricetta(ingrediente: str) -> str:
        "Cerca ricette che usano un ingrediente."
        ...

    app.main()   # `python -m ricette manifesto` scrive il manifesto, `… esegui cerca_ricetta …` la esegue

    aios-abilita elenca | valida FILE | installa FILE | prova NOME [chiave=valore…]
"""

from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .fastpath import Intent, normalize
from .tools.base import Tool

TYPES = {"string": str, "integer": int, "number": float, "boolean": bool}
NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
APP_ID = re.compile(r"^[A-Za-z][\w.-]{2,100}$")
MAX_OUTPUT = 4000
TIMEOUT = 30


class ManifestError(ValueError):
    pass


def ability_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return [Path("/usr/share/aios/abilita"), Path("/etc/aios/abilita"), home / "aios" / "abilita"]


@dataclass
class Ability:
    app: str
    app_name: str
    name: str
    description: str
    params: dict[str, dict[str, Any]]
    required: list[str]
    phrases: list[str] = field(default_factory=list)
    run: list[str] = field(default_factory=list)
    dbus: dict[str, str] = field(default_factory=dict)
    confirm: bool = False
    private: bool = False
    sends_out: bool = False

    @property
    def tool_name(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", self.app.lower().rsplit(".", 1)[-1]).strip("_")[:20]
        return f"app_{slug}__{self.name}"

    def schema(self) -> dict[str, Any]:
        props = {}
        for key, spec in self.params.items():
            prop: dict[str, Any] = {"type": spec["tipo"], "description": spec.get("descrizione", key)}
            if spec.get("valori"):
                prop["enum"] = spec["valori"]
            props[key] = prop
        return {"type": "object", "properties": props, "required": self.required}


def parse_manifest(doc: Any) -> list[Ability]:
    """Controlla un manifesto: solo campi previsti, nomi e tipi validi, un solo modo di chiamata."""
    if not isinstance(doc, dict) or not APP_ID.match(str(doc.get("app", ""))):
        raise ManifestError("manca «app» (identificativo dell'app, es. org.esempio.Ricette)")
    abilities = []
    for raw in doc.get("abilita", []):
        if not isinstance(raw, dict) or not NAME.match(str(raw.get("nome", ""))):
            raise ManifestError(f"nome di abilità non valido: {raw.get('nome') if isinstance(raw, dict) else raw!r}")
        params = raw.get("parametri", {}) or {}
        if not isinstance(params, dict):
            raise ManifestError("«parametri» deve essere un oggetto")
        for key, spec in params.items():
            if not NAME.match(key) or not isinstance(spec, dict) or spec.get("tipo") not in TYPES:
                raise ManifestError(f"{raw['nome']}: parametro «{key}» non valido (tipi: {', '.join(TYPES)})")
        required = [r for r in raw.get("obbligatori", list(params)) if r in params]
        run, dbus = raw.get("esegui") or [], raw.get("dbus") or {}
        if bool(run) == bool(dbus):
            raise ManifestError(f"{raw['nome']}: serve «esegui» oppure «dbus» (uno solo)")
        if run and (not isinstance(run, list) or not all(isinstance(a, str) for a in run) or not run[0] or
                    "{" in run[0]):
            raise ManifestError(f"{raw['nome']}: «esegui» è un elenco di argomenti e il programma è fisso")
        unknown = {p for a in run for p in re.findall(r"\{(\w+)\}", a)} - set(params)
        if unknown:
            raise ManifestError(f"{raw['nome']}: segnaposto sconosciuti {sorted(unknown)}")
        if dbus and not all(isinstance(dbus.get(k), str) and dbus[k] for k in ("servizio", "percorso", "interfaccia", "metodo")):
            raise ManifestError(f"{raw['nome']}: «dbus» richiede servizio, percorso, interfaccia e metodo")
        phrases = [p for p in raw.get("frasi", []) if isinstance(p, str) and 3 <= len(p) <= 120][:20]
        abilities.append(Ability(str(doc["app"]), str(doc.get("nome", doc["app"]))[:60], raw["nome"],
                                 str(raw.get("descrizione", raw["nome"]))[:300], params, required, phrases, run, dbus,
                                 bool(raw.get("conferma")), bool(raw.get("privato")), bool(raw.get("invia_fuori"))))
    if not abilities:
        raise ManifestError("il manifesto non dichiara abilità")
    return abilities


def load_all(dirs: list[Path] | None = None, errors: list[str] | None = None) -> list[Ability]:
    found: dict[str, Ability] = {}
    for folder in dirs or ability_dirs():
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                for ability in parse_manifest(json.loads(path.read_text())):
                    found[ability.tool_name] = ability  # le cartelle dell'utente vengono dopo: vincono
            except (OSError, ValueError) as exc:
                if errors is not None:
                    errors.append(f"{path.name}: {exc}")
    return list(found.values())


def coerce(ability: Ability, args: dict[str, Any]) -> dict[str, Any]:
    clean = {}
    for key, spec in ability.params.items():
        if key not in args or args[key] in (None, ""):
            if key in ability.required:
                raise ManifestError(f"manca «{key}»")
            continue
        value = args[key]
        try:
            value = TYPES[spec["tipo"]](value) if spec["tipo"] != "boolean" else str(value).lower() in ("true", "sì", "si", "1")
        except (TypeError, ValueError) as exc:
            raise ManifestError(f"«{key}» deve essere {spec['tipo']}") from exc
        if spec.get("valori") and value not in spec["valori"]:
            raise ManifestError(f"«{key}» deve essere uno di {spec['valori']}")
        if isinstance(value, str) and len(value) > 2000:
            raise ManifestError(f"«{key}» è troppo lungo")
        clean[key] = value
    return clean


def build_argv(ability: Ability, args: dict[str, Any]) -> list[str]:
    argv = []
    for part in ability.run:
        names = re.findall(r"\{(\w+)\}", part)
        if any(n not in args for n in names):
            continue  # parametro facoltativo non dato: l'argomento si omette
        value = re.sub(r"\{(\w+)\}", lambda m: str(args[m.group(1)]), part)
        if names and value.startswith("-") and not part.startswith("-"):
            raise ManifestError("un valore non può cominciare con «-»")  # niente opzioni nascoste
        argv.append(value)
    return argv


def call(ability: Ability, args: dict[str, Any], run: Callable[[list[str]], tuple[int, str]] | None = None) -> str:
    try:
        clean = coerce(ability, args)
        if ability.run:
            cmd = build_argv(ability, clean)
        else:
            d = ability.dbus
            cmd = ["gdbus", "call", "--session", "--dest", d["servizio"], "--object-path", d["percorso"],
                   "--method", f"{d['interfaccia']}.{d['metodo']}", json.dumps(json.dumps(clean, ensure_ascii=False))]
    except ManifestError as exc:
        return f"{ability.app_name}: {exc}"
    code, out = (run or _run)(cmd)
    if ability.dbus and code == 0:
        m = re.match(r"^\('(.*)',\)\s*$", out.strip(), re.S)  # risposta di gdbus: ('testo',)
        out = m.group(1).encode().decode("unicode_escape") if m else out
    out = out.strip()[:MAX_OUTPUT]
    if code != 0:
        return f"{ability.app_name} ha risposto con un errore: {out[-300:]}"
    return out or f"Fatto ({ability.app_name})."


def _run(cmd: list[str]) -> tuple[int, str]:
    if not shutil.which(cmd[0]):
        return 127, f"programma non trovato: {cmd[0]}"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, "l'app non ha risposto in tempo"
    return p.returncode, p.stdout + (p.stderr if p.returncode else "")


def make_tools(abilities: list[Ability] | None = None, run: Callable[[list[str]], tuple[int, str]] | None = None) -> list[Tool]:
    abilities = load_all() if abilities is None else abilities
    tools = []
    for a in abilities:
        tools.append(Tool(a.tool_name, f"[{a.app_name}] {a.description}", a.schema(),
                          (lambda ability: lambda **kw: call(ability, kw, run))(a),
                          requires_confirmation=a.confirm, reads_private=a.private, sends_out=a.sends_out,
                          external=True))
    return tools


def phrase_pattern(phrase: str) -> re.Pattern[str]:
    parts = re.split(r"(\{\w+\})", normalize(phrase))
    regex = "".join(f"(?P<{p[1:-1]}>.+?)" if re.fullmatch(r"\{\w+\}", p) else re.escape(p) for p in parts)
    return re.compile(f"^{regex}$")


class AppsRouter:
    """Le frasi d'esempio delle app riconosciute all'istante («metti un timer di {minuti} minuti»)."""

    def __init__(self, abilities: list[Ability] | None = None):
        abilities = load_all() if abilities is None else abilities
        self.patterns = [(phrase_pattern(p), a) for a in abilities for p in a.phrases]

    def match(self, text: str) -> Intent | None:
        low = normalize(text).rstrip("?!.")
        for pattern, ability in self.patterns:
            m = pattern.match(low)
            if m:
                args = {k: v.strip() for k, v in m.groupdict().items()}
                original = re.compile(pattern.pattern, re.I).match(" ".join(text.split()).rstrip("?!."))
                if original:  # i valori con le maiuscole originali
                    args = {k: v.strip() for k, v in original.groupdict().items()}
                return Intent(ability.tool_name, args)
        return None


# --- aiuto per gli sviluppatori Python ---------------------------------------------------------------


class Abilities:
    """Dichiara le abilità con un decoratore: il manifesto si genera dalla firma della funzione."""

    PY_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}

    def __init__(self, app: str, name: str, command: list[str] | None = None):
        self.app, self.name = app, name
        self.command = command or [sys.executable, "-m", app.rsplit(".", 1)[-1].lower()]
        self.functions: dict[str, tuple[Callable[..., Any], dict[str, Any]]] = {}

    def ability(self, frasi: list[str] | None = None, conferma: bool = False, privato: bool = False,
                invia_fuori: bool = False) -> Callable:
        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.functions[fn.__name__] = (fn, {"frasi": frasi or [], "conferma": conferma, "privato": privato,
                                                "invia_fuori": invia_fuori})
            return fn
        return register

    def manifest(self) -> dict[str, Any]:
        out = []
        for name, (fn, opts) in self.functions.items():
            sig = inspect.signature(fn)
            params = {p.name: {"tipo": self.PY_TYPES.get(p.annotation if not isinstance(p.annotation, str)
                                                         else {"str": str, "int": int, "float": float, "bool": bool}.get(p.annotation, str), "string"),
                               "descrizione": p.name.replace("_", " ")} for p in sig.parameters.values()}
            required = [p.name for p in sig.parameters.values() if p.default is inspect.Parameter.empty]
            run = [*self.command, "esegui", name, *[f"--{k}={{{k}}}" for k in params]]
            out.append({"nome": name, "descrizione": inspect.getdoc(fn) or name, "parametri": params,
                        "obbligatori": required, "esegui": run, **opts})
        return {"app": self.app, "nome": self.name, "versione": 1, "abilita": out}

    def main(self, argv: list[str] | None = None) -> int:
        args = list(sys.argv[1:] if argv is None else argv)
        if args[:1] == ["manifesto"]:
            print(json.dumps(self.manifest(), ensure_ascii=False, indent=1))
            return 0
        if args[:1] == ["esegui"] and len(args) > 1 and args[1] in self.functions:
            fn, _ = self.functions[args[1]]
            kwargs = dict(a[2:].split("=", 1) for a in args[2:] if a.startswith("--") and "=" in a)
            hints = inspect.signature(fn).parameters
            for k, v in list(kwargs.items()):
                kind = hints[k].annotation if k in hints else str
                kind = {"str": str, "int": int, "float": float, "bool": bool}.get(kind, kind) if isinstance(kind, str) else kind
                kwargs[k] = (v.lower() in ("true", "1", "sì")) if kind is bool else kind(v) if kind in (int, float) else v
            print(fn(**kwargs))
            return 0
        print("uso: manifesto | esegui NOME --param=valore…", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv) or ["elenca"]
    if args[0] == "elenca":
        errors: list[str] = []
        for a in load_all(errors=errors):
            print(f"{a.app_name}: {a.name} — {a.description}" + (f"  (frasi: {'; '.join(a.phrases[:2])})" if a.phrases else ""))
        for e in errors:
            print("⚠️ " + e)
    elif args[0] in ("valida", "installa") and len(args) > 1:
        path = Path(args[1])
        abilities = parse_manifest(json.loads(path.read_text()))
        print(f"Valido: {len(abilities)} abilità di {abilities[0].app_name}.")
        if args[0] == "installa":
            dest = ability_dirs()[-1]
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, dest / f"{abilities[0].app}.json")
            print(f"Installato in {dest}: il copilota le userà dal prossimo avvio.")
    elif args[0] == "prova" and len(args) > 1:
        ability = next((a for a in load_all() if args[1] in (a.name, a.tool_name)), None)
        if ability is None:
            print("Abilità non trovata.")
            return 1
        print(call(ability, dict(a.split("=", 1) for a in args[2:] if "=" in a)))
    else:
        print("aios-abilita [elenca | valida FILE | installa FILE | prova NOME chiave=valore…]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
