"""Misura il controllo di qualità delle personalizzazioni, con aios-nucleo (llama-server con mmproj e adattatori) avviato.

    python addestramento/valuta_verifiche.py --dati dati-verifica --url http://127.0.0.1:11436

Su esempi mai visti (prova-verifica.jsonl):
- giudizio: «riuscita sì/no» giusto; falsi allarmi: modifiche riuscite giudicate sbagliate; sfuggite: sbagliate
  giudicate riuscite;
- problemi: quanti dei problemi veri riconosce e quanti di quelli che segnala sono veri;
- il giudizio per tipo di modifica (colore, posizione, testo, orologio…).
Confronta il modello senza adattatore con l'adattatore «verifica»; esce una tabella in Markdown.
Codice d'uscita 3 se l'adattatore non migliora abbastanza (allora non si pubblica).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aios_copilot.nucleo import SYSTEM_VERIFICA, VERIFY_SCHEMA, Nucleo  # noqa: E402


def run(n: Nucleo, base: Path, rows: list[dict], adapter: str | None) -> dict:
    tot: Counter = Counter()
    by_kind: dict[str, list[int]] = defaultdict(list)
    took = 0.0
    for r in rows:
        want = json.loads(r["risposta"])
        t = time.monotonic()
        try:
            got = json.loads(n.see([(base / i).read_bytes() for i in r["immagini"]], r["prompt"], adapter,
                                   SYSTEM_VERIFICA, VERIFY_SCHEMA, 120))
        except (ValueError, OSError):
            got = {}
        took += time.monotonic() - t
        said_ok = bool(got.get("fatto")) and not got.get("problemi")
        right = said_ok == bool(want["fatto"])
        tot["giusti"] += right
        tot["esempi"] += 1
        by_kind[r.get("modifica", "?")].append(int(right))
        if want["fatto"]:
            tot["riuscite"] += 1
            tot["falsi_allarmi"] += not said_ok
        else:
            tot["sbagliate"] += 1
            tot["sfuggite"] += said_ok
        wp, gp = set(want["problemi"]), set(got.get("problemi") or [])
        tot["trovati"] += len(wp & gp)
        tot["veri"] += len(wp)
        tot["segnalati"] += len(gp)
    return {"giudizio": tot["giusti"] / max(tot["esempi"], 1),
            "falsi_allarmi": tot["falsi_allarmi"] / max(tot["riuscite"], 1),
            "sfuggite": tot["sfuggite"] / max(tot["sbagliate"], 1),
            "richiamo": tot["trovati"] / max(tot["veri"], 1), "precisione": tot["trovati"] / max(tot["segnalati"], 1),
            "per_tipo": {k: sum(v) / len(v) for k, v in sorted(by_kind.items())}, "tempo": took / max(len(rows), 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dati", default="dati-verifica")
    ap.add_argument("--url", default="http://127.0.0.1:11436")
    ap.add_argument("--solo-base", action="store_true")
    args = ap.parse_args()
    base = Path(args.dati)
    rows = [json.loads(line) for line in (base / "prova-verifica.jsonl").read_text().splitlines() if line.strip()]
    n = Nucleo(args.url, timeout=300)
    before = run(n, base, rows, None)
    after = before if args.solo_base else run(n, base, rows, "verifica")
    print(f"Esempi mai visti: {len(rows)} ({sum(json.loads(r['risposta'])['fatto'] for r in rows)} riusciti).\n")
    print("| misura | senza adattatore | con adattatore |")
    print("|---|---|---|")
    print(f"| giudizio giusto (riuscita sì/no) | {before['giudizio']:.0%} | {after['giudizio']:.0%} |")
    print(f"| falsi allarmi (riuscite giudicate sbagliate) | {before['falsi_allarmi']:.0%} | {after['falsi_allarmi']:.0%} |")
    print(f"| sfuggite (sbagliate giudicate riuscite) | {before['sfuggite']:.0%} | {after['sfuggite']:.0%} |")
    print(f"| problemi veri riconosciuti | {before['richiamo']:.0%} | {after['richiamo']:.0%} |")
    print(f"| problemi segnalati che erano veri | {before['precisione']:.0%} | {after['precisione']:.0%} |")
    print(f"| tempo medio | {before['tempo']:.1f} s | {after['tempo']:.1f} s |")
    print("\n| giudizio giusto per tipo di modifica | senza | con |\n|---|---|---|")
    for k in after["per_tipo"]:
        print(f"| {k} | {before['per_tipo'].get(k, 0):.0%} | {after['per_tipo'][k]:.0%} |")
    if args.solo_base:
        return 0
    good = after["giudizio"] >= max(before["giudizio"] + 0.15, 0.65)
    if not good:
        print("\nL'adattatore «verifica» non migliora abbastanza il giudizio: non si pubblica.")
    return 0 if good else 3


if __name__ == "__main__":
    raise SystemExit(main())
