"""Misura il controllo delle schermate, con aios-nucleo (llama-server con mmproj e adattatori) già avviato.

    python addestramento/valuta_schermate.py --dati dati-schermate --url http://127.0.0.1:11436

- orologio: ora letta giusta (entro 2 minuti) sulle schermate con un orologio a lancette; «inventati»: quante volte
  vede un orologio che non c'è;
- guasti: schermate giudicate bene (con o senza guasti), e quanti dei guasti veri trova (per tipo).
Confronta il modello senza adattatore con l'adattatore «schermate»; esce una tabella in Markdown.
Codice d'uscita 3 se l'adattatore non migliora (allora non si pubblica).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aios_copilot.nucleo import PROMPT_SCHERMATA, SCREEN_SCHEMA, SYSTEM_SCHERMATA, Nucleo  # noqa: E402


def minutes(text: str) -> int | None:
    try:
        h, m = (int(x) for x in str(text).strip().split(":")[:2])
        return (h % 12) * 60 + m
    except ValueError:
        return None


def score(got: dict, want: dict) -> dict[str, float]:
    s: dict[str, float] = {}
    w, g = minutes(want.get("orologio", "")), minutes(got.get("orologio", ""))
    if w is not None:
        s["orologio"] = float(g is not None and min(abs(g - w), 720 - abs(g - w)) <= 2)
    else:
        s["inventati"] = float(bool(str(got.get("orologio", "")).strip()))
    wp = Counter(p["tipo"] for p in want.get("problemi", []))
    gp = Counter(p.get("tipo") for p in got.get("problemi", []) if isinstance(p, dict))
    s["giudizio"] = float(bool(wp) == bool(gp))
    s["trovati"] = sum((wp & gp).values())
    s["veri"] = sum(wp.values())
    s["segnalati"] = sum(gp.values())
    return s


def run(n: Nucleo, base: Path, rows: list[dict], adapter: str | None) -> dict[str, float]:
    tot: Counter = Counter()
    count: Counter = Counter()
    took = 0.0
    for r in rows:
        t = time.monotonic()
        try:
            got = json.loads(n.see((base / r["immagine"]).read_bytes(), r["prompt"], adapter, SYSTEM_SCHERMATA, SCREEN_SCHEMA, 300))
        except (ValueError, OSError):
            got = {}
        took += time.monotonic() - t
        for k, v in score(got if isinstance(got, dict) else {}, json.loads(r["risposta"])).items():
            tot[k] += v
            count[k] += 1
    return {"orologio": tot["orologio"] / max(count["orologio"], 1), "inventati": tot["inventati"] / max(count["inventati"], 1),
            "giudizio": tot["giudizio"] / max(count["giudizio"], 1), "richiamo": tot["trovati"] / max(tot["veri"], 1),
            "precisione": tot["trovati"] / max(tot["segnalati"], 1), "tempo": took / max(len(rows), 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dati", default="dati-schermate")
    ap.add_argument("--url", default="http://127.0.0.1:11436")
    ap.add_argument("--solo-base", action="store_true", help="misura solo il modello senza adattatore")
    args = ap.parse_args()
    base = Path(args.dati)
    rows = [json.loads(line) for line in (base / "prova-schermate.jsonl").read_text().splitlines() if line.strip()]
    assert all(r["prompt"] == PROMPT_SCHERMATA for r in rows)
    n = Nucleo(args.url, timeout=300)
    before = run(n, base, rows, None)
    after = before if args.solo_base else run(n, base, rows, "schermate")
    print("| misura | senza adattatore | con adattatore |")
    print("|---|---|---|")
    print(f"| ora delle lancette letta giusta | {before['orologio']:.0%} | {after['orologio']:.0%} |")
    print(f"| orologi inventati (dove non c'è) | {before['inventati']:.0%} | {after['inventati']:.0%} |")
    print(f"| schermate giudicate bene (guasti sì/no) | {before['giudizio']:.0%} | {after['giudizio']:.0%} |")
    print(f"| guasti veri trovati | {before['richiamo']:.0%} | {after['richiamo']:.0%} |")
    print(f"| guasti segnalati che erano veri | {before['precisione']:.0%} | {after['precisione']:.0%} |")
    print(f"| tempo medio per schermata | {before['tempo']:.1f} s | {after['tempo']:.1f} s |")
    if args.solo_base:
        return 0
    # si pubblica per leggere gli orologi (i guasti di impaginazione li trovano i controlli sulla pagina):
    # deve leggere molto meglio e non vedere orologi che non ci sono
    good = after["orologio"] >= before["orologio"] + 0.2 and after["inventati"] <= before["inventati"] + 0.05
    if not good:
        print("\nL'adattatore schermate non migliora abbastanza: non si pubblica.")
    return 0 if good else 3


if __name__ == "__main__":
    raise SystemExit(main())
