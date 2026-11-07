"""Prepara gli esempi per gli adattatori del nucleo: python addestramento/dati.py --uscita dati/

Ambiti, azioni e campi si leggono dagli strumenti veri di Nova (gli stessi dello smistatore), così gli
adattatori restano allineati al codice. Le frasi vengono da frasi.py, dal catalogo del livello 1
(semantic.py) e dal banco di prova (evaldata.py). Escono:
  smistamento.jsonl, campi.jsonl         per l'addestramento
  prova-smistamento.jsonl, prova-campi.jsonl  frasi tenute da parte, per misurare il risultato
Ogni riga: {"prompt": …, "risposta": …, "compito": "ambito|azione|campi", "opzioni": […]}.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aios_copilot import nucleo  # noqa: E402
from aios_copilot.smistatore import NO_ACTION  # noqa: E402
from frasi import CHIACCHIERA, ESEMPI, QUANDO, VALORI  # noqa: E402


def nova_tools() -> tuple[dict[str, list[str]], dict[str, Any]]:
    """({ambito: [strumenti]}, {nome: Tool}) come li vede lo smistatore, senza modelli né servizi."""
    os.environ.setdefault("HOME", tempfile.mkdtemp())
    import aios_copilot.__main__ as main

    class Silent:
        supports_stream = False

        def chat(self, *a: Any, **k: Any) -> dict[str, str]:
            return {"content": ""}

        def warmup(self, *a: Any, **k: Any) -> None:
            pass

    main.make_client = lambda model=None: Silent()
    os.environ["AIOS_NUCLEO"] = "spento"
    agent = main.make_agent(lambda *a, **k: True)
    smistatore = agent.narrow.__self__
    domains = {name: sorted(t for t in d.tools if t in agent.tools) for name, d in smistatore.domains.items()}
    return domains, agent.tools


def variants(text: str, rng: random.Random) -> str:
    """Piccole variazioni di come si scrive o si dice una frase."""
    r = rng.random()
    if r < 0.15:
        text = "Nova, " + text[0].lower() + text[1:]
    elif r < 0.25:
        text = text.rstrip("?.!") + " per favore"
    elif r < 0.35:
        text = text.lower().rstrip("?.!")
    elif r < 0.42:
        text = text[0].upper() + text[1:]
    return text


def fill(template: str, fields: dict[str, str], now: datetime, rng: random.Random) -> tuple[str, dict[str, str]]:
    chosen: dict[str, str] = {}
    when = rng.choice(QUANDO)

    def put(m: re.Match[str]) -> str:
        key = m.group(1)
        if key == "quando":
            return when[0]
        chosen.setdefault(key, rng.choice(VALORI[key]))
        return chosen[key]

    text = re.sub(r"\{(\w+)\}", put, template)
    args = {}
    for k, v in fields.items():
        args[k] = re.sub(r"\{(\w+)\}", lambda m: when[1](now) if m.group(1) == "quando" else chosen.get(m.group(1), ""), v)
    return text, args


def random_now(rng: random.Random) -> datetime:
    base = datetime(2026, 1, 5, 8, 0)
    return base + timedelta(days=rng.randrange(365), hours=rng.randrange(14), minutes=rng.choice([0, 7, 15, 30, 42]))


def build(seed: int = 7, per_template: int = 6) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(seed)
    domains, tools = nova_tools()
    domain_of = {t: d for d, names in domains.items() for t in names}
    rows: dict[str, list[dict[str, Any]]] = {"smistamento": [], "campi": []}
    skipped: set[str] = set()

    def add(text: str, tool: str | None, args: dict[str, Any] | None, now: datetime, held_out: bool) -> None:
        split = "prova" if held_out else "addestra"
        if tool is None:  # chiacchiera: ambito, e «nessuna azione» se capita tra le azioni di un ambito
            rows["smistamento"].append({"prompt": nucleo.prompt_ambito(text), "risposta": "chiacchiera", "compito": "ambito",
                                        "opzioni": list(domains), "parte": split})
            other = rng.choice([d for d in domains if domains[d]])
            names = [*domains[other], NO_ACTION]
            rows["smistamento"].append({"prompt": nucleo.prompt_azione(text, names), "risposta": NO_ACTION,
                                        "compito": "azione", "opzioni": names, "parte": split})
            return
        if tool not in domain_of:
            skipped.add(tool)
            return
        domain = domain_of[tool]
        rows["smistamento"].append({"prompt": nucleo.prompt_ambito(text), "risposta": domain, "compito": "ambito",
                                    "opzioni": list(domains), "parte": split})
        names = [*domains[domain], NO_ACTION]
        rows["smistamento"].append({"prompt": nucleo.prompt_azione(text, names), "risposta": tool, "compito": "azione",
                                    "opzioni": names, "parte": split})
        props = tools[tool].parameters.get("properties", {})
        if props and args is not None:
            clean = {k: args[k] for k in props if args.get(k) not in (None, "")}
            for k, v in clean.items():
                if props[k].get("enum") and v not in props[k]["enum"]:
                    raise ValueError(f"{tool}.{k}: «{v}» non è tra {props[k]['enum']}")
            rows["campi"].append({"prompt": nucleo.prompt_campi(text, tool, tools[tool].description, props, now),
                                  "risposta": json.dumps(clean, ensure_ascii=False), "compito": "campi",
                                  "opzioni": [], "parte": split})

    for i, (tool, template, fields) in enumerate(ESEMPI):
        for k in range(per_template if "{" in template else 2):
            now = random_now(rng)
            text, args = fill(template, fields, now, rng)
            add(variants(text, rng), tool, args, now, held_out=(k == per_template - 1 and "{" in template))
    from aios_copilot.evaldata import IN_SCOPE
    from aios_copilot.semantic import CATALOG

    by_name = {s.name: s for s in CATALOG}
    for spec in CATALOG:
        static = None if callable(spec.args) else dict(spec.args)
        for ex in spec.examples:
            add(variants(ex, rng), spec.tool, static, random_now(rng), held_out=False)
    for text, name in IN_SCOPE:  # frasi mai viste dal livello 1: buone per misurare
        spec = by_name[name]
        add(text, spec.tool, None if callable(spec.args) else dict(spec.args), random_now(rng), held_out=True)
    for j, text in enumerate(CHIACCHIERA):
        add(variants(text, rng), None, None, random_now(rng), held_out=(j % 5 == 4))
        add(variants(text, rng), None, None, random_now(rng), held_out=False)
    if skipped:
        print("strumenti non più in Nova (frasi ignorate):", ", ".join(sorted(skipped)), file=sys.stderr)
    for v in rows.values():
        rng.shuffle(v)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uscita", default="dati")
    ap.add_argument("--per-modello", type=int, default=6, help="variazioni per ogni modello di frase")
    args = ap.parse_args()
    out = Path(args.uscita)
    out.mkdir(parents=True, exist_ok=True)
    rows = build(per_template=args.per_modello)
    for adapter, items in rows.items():
        for part, prefix in (("addestra", ""), ("prova", "prova-")):
            chosen = [r for r in items if r["parte"] == part]
            with open(out / f"{prefix}{adapter}.jsonl", "w") as f:
                for r in chosen:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"{prefix}{adapter}: {len(chosen)} esempi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
