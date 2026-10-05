"""Il codice personale: AIOS che si riprogramma su richiesta dell'utente.

Il codice di AIOS nell'immagine (immutabile) è la «base». Quando l'utente chiede a Nova di cambiare qualcosa
(«voglio l'orologio rotondo»), Nova crea una copia del codice in ~/.local/share/aios/codice, un repository git:

- ramo «base»: il codice originale, uno scatto per ogni versione di AIOS;
- ramo «mio»: la base più le modifiche dell'utente, una per richiesta, con la richiesta come descrizione.

Il pacchetto (aios_copilot/__init__.py) gira dalla copia se è attiva. Ogni modifica si annulla da sola
(git revert); «AIOS originale» spegne la copia senza cancellarla. Quando arriva una nuova versione di AIOS si
aggiorna la base e le modifiche ci vanno sopra (git merge); se non ci stanno, la copia resta ferma e si usa la
base finché Nova non le riapplica.

Modalità sicura: ogni avvio della shell si conta (aios_copilot/__init__.py, prima di caricare la copia); la shell,
quando funziona, azzera il conto. Al terzo avvio non riuscito di fila la copia è «guasta» e AIOS riparte dal codice
originale (e lo dice).

Condividere: ogni modifica si esporta in un file «.aios» (la richiesta, il riassunto e il cambiamento al codice)
da dare a un altro utente, anche sullo stesso PC; chi lo importa lo vede, e se lo accetta lo applica alla sua
copia. Tutto in locale con git dentro il PC: nessun account esterno.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import BASE_DIR

AUTHOR = ["-c", "user.name=Nova", "-c", "user.email=nova@aios.local", "-c", "commit.gpgsign=false"]


def root() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "codice"


def state_path() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "aios" / "codice.json"


def state() -> dict[str, Any]:
    try:
        data = json.loads(state_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(**changes: Any) -> dict[str, Any]:
    s = {**state(), **changes}
    state_path().parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(state_path())
    return s


def base_version() -> str:
    try:
        return Path("/usr/share/aios/versione").read_text().strip()
    except OSError:
        return "sviluppo"


def git(*args: str, check: bool = True) -> str:
    p = subprocess.run(["git", *AUTHOR, *args], cwd=root(), capture_output=True, text=True, timeout=120)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def _copy_base(dest: Path, base: Path | None = None) -> None:
    base = base or BASE_DIR
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(base, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def exists() -> bool:
    return (root() / ".git").is_dir() and (root() / "aios_copilot" / "__init__.py").exists()


def ensure(base: Path | None = None) -> None:
    """Crea la copia personale (la prima volta) e la tiene allineata alla versione di AIOS installata."""
    if not shutil.which("git"):
        raise RuntimeError("manca git nel sistema")
    if not exists():
        root().mkdir(parents=True, exist_ok=True)
        _copy_base(root() / "aios_copilot", base)
        (root() / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        git("init", "-q", "-b", "base")
        git("add", "-A")
        git("commit", "-q", "-m", f"AIOS {base_version()} (base)")
        git("branch", "mio")
        git("checkout", "-q", "mio")
        save_state(attivo=False, guasto=False, conflitto=False, base=base_version(), avvii=0)
        return
    sync_base(base)


def sync_base(base: Path | None = None) -> str:
    """Nuova versione di AIOS: si aggiorna la base e le modifiche dell'utente ci vanno sopra."""
    version = base_version()
    if not exists() or state().get("base") == version:
        return ""
    git("checkout", "-q", "-f", "base")
    _copy_base(root() / "aios_copilot", base)
    git("add", "-A")
    if git("status", "--porcelain").strip():
        git("commit", "-q", "-m", f"AIOS {version} (base)")
    git("checkout", "-q", "-f", "mio")
    p = subprocess.run(["git", *AUTHOR, "merge", "-q", "--no-edit", "-m", f"Personalizzazioni sopra AIOS {version}", "base"],
                       cwd=root(), capture_output=True, text=True)
    if p.returncode != 0:
        git("merge", "--abort", check=False)
        save_state(conflitto=True, base=version)
        return "conflitto"
    save_state(conflitto=False, base=version)
    return "aggiornata"


def history() -> list[dict[str, Any]]:
    """Le modifiche dell'utente, dalla più recente."""
    if not exists():
        return []
    out = git("log", "--no-merges", "--format=%H%x1f%ct%x1f%s%x1f%b%x1e", "base..mio", check=False)
    items, undone = [], set()
    for rec in out.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) >= 3 and parts[0]:
            body = parts[3].strip() if len(parts) > 3 else ""
            m = re.search(r"This reverts commit ([0-9a-f]{40})", body)
            if m:  # un «annulla»: lui e la modifica che annulla non si mostrano più
                undone.update((m.group(1), parts[0]))
                continue
            items.append({"hash": parts[0], "id": parts[0][:12], "quando": int(parts[1]), "richiesta": parts[2], "dettagli": body})
    return [i for i in items if i["hash"] not in undone]


def has_changes() -> bool:
    return bool(history())


def commit(request: str, summary: str, retouch: dict[str, Any] | None = None) -> str:
    """Salva la modifica. Un ritocco (la matitina) della personalizzazione più recente la aggiorna e resta una sola;
    il ritocco di una più vecchia diventa una modifica a parte, col nome di quella che ritocca."""
    git("add", "-A")
    if not git("status", "--porcelain").strip():
        return ""
    if retouch and retouch["hash"] == git("rev-parse", "HEAD").strip():
        body = (retouch["dettagli"] + f"\n\nRitocco: {request}. {summary}").strip()
        git("commit", "-q", "--amend", "-m", retouch["richiesta"], "-m", body[:4000])
    elif retouch:
        git("commit", "-q", "-m", f"Ritocco a «{retouch['richiesta'][:120]}»: {request}"[:200], "-m", summary[:2000])
    else:
        git("commit", "-q", "-m", request[:200], "-m", summary[:2000])
    save_state(attivo=True, guasto=False, avvii=0)
    return git("rev-parse", "--short=12", "HEAD").strip()


def show(item: dict[str, Any], limit: int = 6000) -> str:
    """Il cambiamento al codice di una personalizzazione (per ritoccarla)."""
    text = git("show", "--format=", "--unified=2", item["hash"], check=False)
    return text[:limit] + ("\n… (continua)" if len(text) > limit else "")


def discard() -> None:
    """Butta le modifiche non salvate (un tentativo andato male)."""
    git("reset", "-q", "--hard", "HEAD", check=False)
    git("clean", "-q", "-fd", check=False)


def changed_files() -> list[str]:
    out = git("status", "--porcelain", "-uall", check=False)
    return [line[3:] for line in out.splitlines() if line.strip()]


def diff() -> str:
    git("add", "-A", "--intent-to-add", check=False)
    return git("diff", check=False)


def find(change: str) -> dict[str, Any] | None:
    """Una modifica per id, o per parole della richiesta («l'orologio»); «ultima» = la più recente."""
    items = history()
    c = change.strip().lower()
    if c in ("", "ultima", "l'ultima", "ultimo"):
        return items[0] if items else None
    return next((h for h in items if h["id"].startswith(c)), None) or \
        next((h for h in items if c in h["richiesta"].lower()), None)


def undo(change: str) -> tuple[bool, str]:
    """Toglie una modifica (anche non l'ultima) lasciando le altre; → (riuscito, richiesta o motivo)."""
    item = find(change)
    if item is None:
        return False, "Non trovo quella personalizzazione."
    discard()
    p = subprocess.run(["git", *AUTHOR, "revert", "--no-edit", item["hash"]], cwd=root(), capture_output=True, text=True)
    if p.returncode != 0:
        git("revert", "--abort", check=False)
        return False, "Una personalizzazione più recente tocca le stesse righe: togli prima quella."
    if not has_changes():
        save_state(attivo=False)
    return True, item["richiesta"]


def reset_all() -> int:
    """Torna ad AIOS originale: le modifiche si tolgono tutte (restano nella storia di git, recuperabili)."""
    n = len(history())
    if exists():
        git("tag", "-f", f"prima-di-azzerare-{int(time.time())}", "mio")
        git("reset", "-q", "--hard", "base")
    save_state(attivo=False, guasto=False, conflitto=False)
    return n


def set_active(on: bool) -> None:
    save_state(attivo=bool(on) and has_changes(), guasto=False, avvii=0)


# --- condividere le personalizzazioni (file .aios, tutto in locale) ----------------------------------------
FORMAT = "aios-personalizzazione/1"


def export(change_id: str, folder: Path | None = None) -> Path:
    """Una modifica → un file .aios da dare a un altro utente."""
    item = next((h for h in history() if h["id"].startswith(change_id)), None)
    if item is None:
        raise ValueError("non trovo quella personalizzazione")
    patch = git("format-patch", "-1", "--stdout", item["id"])
    folder = folder or (Path.home() / "Personalizzazioni")
    folder.mkdir(parents=True, exist_ok=True)
    name = "".join(c if c.isalnum() or c in " -" else " " for c in item["richiesta"])[:50].strip() or "personalizzazione"
    dest = folder / f"{name}.aios"
    n = 2
    while dest.exists():
        dest, n = folder / f"{name} ({n}).aios", n + 1
    dest.write_text(json.dumps({"formato": FORMAT, "richiesta": item["richiesta"], "riassunto": item["dettagli"],
                                "versione_aios": state().get("base", base_version()), "quando": item["quando"],
                                "patch": patch}, ensure_ascii=False, indent=1))
    return dest


def read_shared(path: Path) -> dict[str, Any]:
    """Il contenuto di un file .aios, per mostrarlo prima di applicarlo: cosa fa, quali file tocca, cosa c'è di delicato."""
    data = json.loads(path.read_text())
    if data.get("formato") != FORMAT or not isinstance(data.get("patch"), str):
        raise ValueError("non è un file di personalizzazione di AIOS")
    files = sorted({line[6:] for line in data["patch"].splitlines() if line.startswith("+++ b/")})
    if any(not f.startswith("aios_copilot/") or f in ("aios_copilot/__init__.py", "aios_copilot/codice.py") for f in files):
        raise ValueError("questa personalizzazione tocca file che non si possono cambiare")
    from .programmatore import risky_lines

    return {"richiesta": data.get("richiesta", ""), "riassunto": data.get("riassunto", ""), "file": files,
            "versione_aios": data.get("versione_aios", ""), "rischi": risky_lines(data["patch"])[:8], "patch": data["patch"]}


def import_shared(path: Path) -> tuple[bool, str]:
    """Applica alla propria copia una personalizzazione ricevuta (git am); se il codice non regge, si toglie."""
    info = read_shared(path)
    ensure()
    discard()
    tmp = root() / ".importa.patch"
    tmp.write_text(info["patch"])
    p = subprocess.run(["git", *AUTHOR, "am", "-q", "-3", str(tmp)], cwd=root(), capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    if p.returncode != 0:
        git("am", "--abort", check=False)
        return False, "Non si adatta alla tua versione di AIOS (le stesse parti sono cambiate in modo diverso)."
    ok, msg = check()
    if not ok:
        git("reset", "-q", "--hard", "HEAD~1")
        return False, "Applicata, il codice non reggeva e l'ho tolta: " + msg
    save_state(attivo=True, guasto=False, avvii=0)
    return True, info["richiesta"]


# --- verifiche prima di salvare una modifica --------------------------------------------------------------
def check(paths: list[str] | None = None) -> tuple[bool, str]:
    """Il codice modificato si compila e i moduli principali si importano (in un processo a parte)."""
    pkg = root() / "aios_copilot"
    for f in pkg.rglob("*.py"):
        try:
            compile(f.read_text(errors="replace"), str(f), "exec")
        except SyntaxError as exc:
            return False, f"Errore di sintassi in {f.relative_to(root())}, riga {exc.lineno}: {exc.msg}"
    for f in pkg.rglob("*.json"):
        try:
            json.loads(f.read_text())
        except ValueError as exc:
            return False, f"JSON non valido in {f.relative_to(root())}: {exc}"
    js = [f for f in pkg.rglob("*.js")] + [f for f in pkg.rglob("*.html")]
    for f in js:
        problem = js_balance(f.read_text(errors="replace"))
        if problem:
            return False, f"{f.relative_to(root())}: {problem}"
    code = ("import sys; sys.path.insert(0, %r); import aios_copilot; assert aios_copilot.__path__[0] == %r, aios_copilot.__path__;"
            " import aios_copilot.shell, aios_copilot.agent, aios_copilot.shell.apps, aios_copilot.__main__") % (str(root()), str(pkg))
    env = {**os.environ, "AIOS_CODICE": "base", "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, env=env, cwd=root())
    if p.returncode != 0:
        return False, "I moduli non si caricano: " + p.stderr.strip()[-600:]
    return True, "Il codice si compila e i moduli principali si caricano."


def js_balance(text: str) -> str:
    """Un controllo grezzo per HTML e JavaScript (niente Node nell'immagine): parentesi e virgolette chiuse."""
    pairs, stack, quote, i, line = {")": "(", "]": "[", "}": "{"}, [], "", 0, 1
    in_script = not text.lstrip().lower().startswith(("<!doctype", "<html"))
    while i < len(text):
        c = text[i]
        if c == "\n":
            line += 1
        if not in_script:  # nell'HTML contano solo gli script e gli stili
            low = text[i:i + 8].lower()
            if low.startswith("<script") or low.startswith("<style"):
                in_script, i = True, text.find(">", i) + 1 or len(text)
                continue
            i += 1
            continue
        if text[i:i + 9].lower() in ("</script>",) or text[i:i + 8].lower() == "</style>":
            if stack:
                return f"parentesi «{stack[-1][0]}» aperta alla riga {stack[-1][1]} e mai chiusa"
            in_script = False
            i += 1
            continue
        if quote:
            if c == "\\":
                i += 2
                continue
            if quote == "`" and text.startswith("${", i):  # espressione dentro un template: si torna al codice
                stack.append(("${", line))
                quote = ""
                i += 2
                continue
            if c == quote:
                quote = ""
            elif quote != "`" and c == "\n":
                return f"virgolette non chiuse alla riga {line - 1}"
            i += 1
            continue
        if text.startswith("//", i) and (i == 0 or text[i - 1] not in ":\\"):
            i = text.find("\n", i)
            i = len(text) if i < 0 else i
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            line += text[i:end].count("\n")
            i = len(text) if end < 0 else end + 2
            continue
        if c in "\"'`":
            quote = c
        elif c in "([{":
            stack.append((c, line))
        elif c == "}" and stack and stack[-1][0] == "${":
            stack.pop()
            quote = "`"
        elif c in ")]}":
            if not stack or stack[-1][0] != pairs[c]:
                return f"parentesi «{c}» in più alla riga {line}"
            stack.pop()
        elif c == "/" and _regex_start(text, i):
            j = i + 1
            klass = False
            while j < len(text) and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    klass = True
                elif text[j] == "]":
                    klass = False
                elif text[j] == "/" and not klass:
                    break
                j += 1
            i = j + 1
            continue
        i += 1
    if stack and in_script:
        return f"parentesi «{stack[-1][0]}» aperta alla riga {stack[-1][1]} e mai chiusa"
    return ""


def _regex_start(text: str, i: int) -> bool:
    j = i - 1
    while j >= 0 and text[j] in " \t":
        j -= 1
    return j < 0 or text[j] in "(,=:[!&|?{};+-*%<>~^\n" or text[max(0, j - 5):j + 1].endswith(("return", "typeof"))


# --- modalità sicura (il conto degli avvii è in aios_copilot/__init__.py) -----------------------------------
def healthy() -> None:
    """La shell funziona: l'avvio è riuscito."""
    if state().get("avvii"):
        save_state(avvii=0)


def describe() -> dict[str, Any]:
    s = state()
    from . import PERSONAL_DIR

    return {"esiste": exists(), "attivo": bool(s.get("attivo")), "guasto": bool(s.get("guasto")),
            "conflitto": bool(s.get("conflitto")), "in_uso": PERSONAL_DIR is not None, "base": s.get("base", base_version()),
            "modifiche": history()}
