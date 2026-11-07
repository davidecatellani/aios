"""La misura del programmatore delle personalizzazioni: il ciclo libero (Programmer) contro quello a fasi
(StagedProgrammer), con lo stesso modello locale, sulla copia vera del codice di SoIA.

    python addestramento/programmatore_prova.py --modello qwen3.5:4b --quante 3 [--compiti colore,saluto]

Ogni compito è una richiesta come la direbbe l'utente, con un controllo automatico del risultato:
- il punto giusto è cambiato nel modo chiesto (un'espressione regolare sul file);
- il codice regge (codice.check: sintassi, parentesi, moduli che si caricano);
- sono cambiati solo i file giusti e non si è tolto altro (righe tolte poche).
Il controllo visivo è spento per tutti e due i modi: si misura il lavoro sul codice. Esce un JSON con i dettagli
(compreso il diff) e una tabella in Markdown.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aios_copilot import codice, programmatore  # noqa: E402
from procedure_prova import Measured  # noqa: E402

HOME = "aios_copilot/shell/home.html"
APPS = "aios_copilot/shell/static/apps.js"
PANNELLO = "aios_copilot/shell/static/pannello.html"
WIDGET = "aios_copilot/widget.py"


@dataclass
class Task:
    name: str
    request: str
    files: tuple[str, ...]               # i file che possono cambiare
    checks: tuple[tuple[str, str, str], ...]  # (nome, file, espressione che deve trovarsi)
    absent: tuple[tuple[str, str, str], ...] = ()  # (nome, file, espressione che non deve più esserci)


TASKS = {t.name: t for t in [
    Task("colore", "Voglio il turchese di SoIA un po' più scuro: usa #1A9E92 al posto di quello di adesso.", (HOME,),
         (("--turchese è #1A9E92", HOME, r"--turchese:\s*#1A9E92\b"),),
         (("il turchese vecchio non c'è più", HOME, r"--turchese:\s*#2EC4B6"),)),
    Task("saluto", "Il saluto grande in alto nella schermata principale è troppo grosso: fallo di 36 pixel.", (HOME,),
         (("#saluto a 36px", HOME, r"#saluto\s*\{[^}]*font-size:\s*36px"),),
         (("niente più 44px", HOME, r"#saluto\s*\{[^}]*font-size:\s*44px"),)),
    Task("ora", "Nel widget dell'orologio l'ora si legge poco: falla più grande, 48 pixel.", (HOME,),
         (("l'ora a 48px", HOME, r"\.orologio-ora\s*\{[^}]*font-size:\s*48px"),)),
    Task("sezione", "In Impostazioni la sezione «Procedure» chiamala «Le mie procedure».", (APPS,),
         (("il nome nuovo", APPS, r'\["procedure",\s*"Le mie procedure"\]'),),
         (("il nome vecchio non c'è più", APPS, r'\["procedure",\s*"Procedure"\]'),)),
    Task("scheda", "Nel pannello degli appunti la scheda «Appunti» deve chiamarsi «Copiati».", (PANNELLO,),
         (("la scheda si chiama Copiati", PANNELLO, r'appunti:\s*"Copiati"'),
          ("la scheda emoji c'è ancora", PANNELLO, r'emoji:\s*"Emoji"'))),
    Task("meteo", "Il meteo dei widget si aggiorna ogni 15 minuti: voglio che si aggiorni ogni 10 minuti.", (WIDGET,),
         (("METEO_EVERY = 600", WIDGET, r"^METEO_EVERY\s*=\s*(600|10\s*\*\s*60|60\s*\*\s*10)\b"),)),
    Task("scuro", "Col tema scuro il testo grigio chiaro si legge poco: nel tema scuro fallo #B5D3D8, "
                  "il tema chiaro lascialo com'è.", (HOME,),
         (("--tenue scuro #B5D3D8", HOME, r"--tenue:\s*#B5D3D8\b"),
          ("--tenue chiaro invariato", HOME, r"--tenue:\s*#5B6E78\b"))),
]}
MAX_REMOVED = 4  # righe tolte in più di quelle cambiate: oltre, si è tolto qualcosa che non si doveva


def score(task: Task, root: Path, changed: list[str], diff: str) -> list[tuple[str, bool]]:
    def text(rel: str) -> str:
        p = root / rel
        return p.read_text(errors="replace") if p.is_file() else ""

    out = [(name, bool(re.search(rx, text(f), re.MULTILINE))) for name, f, rx in task.checks]
    out += [(name, not re.search(rx, text(f), re.MULTILINE)) for name, f, rx in task.absent]
    ok, _ = codice.check()
    out.append(("il codice regge", ok))
    out.append(("solo i file giusti", bool(changed) and set(changed) <= set(task.files)))
    added = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    out.append(("non ha tolto altro", removed <= added + MAX_REMOVED))
    return out


def run_one(task: Task, mode: str, model: Measured) -> dict[str, Any]:
    codice.discard()
    model.calls = []
    kind = programmatore.StagedProgrammer if mode == "fasi" else programmatore.Programmer
    steps: list[str] = []
    start = time.monotonic()
    ok, summary, error = False, "", ""
    try:
        ok, summary = kind(model, on_step=steps.append, eyes=None).run(task.request)
    except Exception as exc:  # un giro rotto conta come sbagliato, non ferma la misura
        error = f"{type(exc).__name__}: {exc}"
    changed, diff = codice.changed_files(), codice.diff()
    checks = score(task, codice.root(), changed, diff)
    codice.discard()
    return {"modo": mode, "finito": ok, "riassunto": summary[:500], "errore": error, "controlli": checks,
            "giusti": sum(c for _, c in checks), "totale": len(checks), "tutto_giusto": all(c for _, c in checks),
            "file": changed, "diff": diff[:4000], "passi": steps, "richieste": len(model.calls),
            "contesto_max": max((c["prompt"] for c in model.calls), default=0),
            "token_letti": sum(c["prompt"] for c in model.calls), "token_scritti": sum(c["risposta"] for c in model.calls),
            "secondi": round(time.monotonic() - start, 1)}


def table(results: list[dict[str, Any]]) -> str:
    lines = ["| compito | modo | lavori tutti giusti | controlli giusti | finiti | richieste | contesto massimo (token) | token letti | secondi |",
             "|---|---|---|---|---|---|---|---|---|"]
    names = list(dict.fromkeys(r["compito"] for r in results))
    for name in names + ["**totale**"]:
        for mode in ("libero", "fasi"):
            rs = [r for r in results if r["modo"] == mode and (name == "**totale**" or r["compito"] == name)]
            if not rs:
                continue
            ok, tot = sum(r["giusti"] for r in rs), sum(r["totale"] for r in rs)
            avg = lambda k: round(sum(r[k] for r in rs) / len(rs))  # noqa: E731
            lines.append(f"| {name} | {mode} | {sum(r['tutto_giusto'] for r in rs)}/{len(rs)} | {ok}/{tot} ({ok / max(tot, 1):.0%}) | "
                         f"{sum(r['finito'] for r in rs)}/{len(rs)} | {avg('richieste')} | {avg('contesto_max')} | "
                         f"{avg('token_letti')} | {avg('secondi')} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modello", default="qwen3.5:4b")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--compiti", default=",".join(TASKS))
    ap.add_argument("--quante", type=int, default=3, help="giri per compito e per modo")
    ap.add_argument("--num-ctx", type=int, default=32768)
    ap.add_argument("--ragiona", action="store_true")
    ap.add_argument("--uscita", default="programmatore.json")
    args = ap.parse_args()
    home = Path(tempfile.mkdtemp(prefix="programmatore-"))
    os.environ["XDG_DATA_HOME"] = str(home / "data")  # la copia personale sta qui, non nella casa vera
    os.environ["XDG_STATE_HOME"] = str(home / "state")
    os.environ["XDG_RUNTIME_DIR"] = str(home / "run")  # nessun Nova che parla: il programmatore non aspetta
    (home / "run").mkdir()
    base = home / "base" / "aios_copilot"
    shutil.copytree(Path(programmatore.__file__).parent, base, ignore=shutil.ignore_patterns("__pycache__"))
    codice.ensure(base)
    model = Measured(args.modello, args.url, args.num_ctx, args.ragiona)
    results = []
    for name in [n.strip() for n in args.compiti.split(",") if n.strip()]:
        for i in range(args.quante):
            for mode in ("libero", "fasi"):
                r = run_one(TASKS[name], mode, model)
                r.update({"compito": name, "giro": i})
                results.append(r)
                bad = [c for c, ok in r["controlli"] if not ok]
                print(f"{name} {i} {mode}: {r['giusti']}/{r['totale']} in {r['secondi']} s, {r['richieste']} richieste"
                      + (f" — sbagliati: {'; '.join(bad)}" if bad else "") + (f" — {r['errore'][:120]}" if r["errore"] else ""),
                      flush=True)
                Path(args.uscita).write_text(json.dumps({"modello": args.modello, "ragiona": args.ragiona, "risultati": results},
                                                        ensure_ascii=False, indent=1))
    print("\n" + table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
