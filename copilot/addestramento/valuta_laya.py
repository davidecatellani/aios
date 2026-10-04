"""Misura Laya sulle frasi tenute da parte (prova-laya.jsonl): il checkpoint di partenza contro quello addestrato.

    python addestramento/valuta_laya.py --dati dati-laya/prova-laya.jsonl --base laya-multilingual --nuovo laya-nova

Esce una tabella in Markdown; codice d'uscita 3 se l'addestrato non migliora (allora non si pubblica).
"""

from __future__ import annotations

import argparse
import json
import time


def score(agent, rows: list[dict]) -> dict[str, tuple[int, int, float]]:
    out: dict[str, list[float]] = {}
    for r in rows:
        t = time.monotonic()
        answers = agent.predict(r["state"], r["questions"])["answers"]
        took = (time.monotonic() - t) / max(len(r["questions"]), 1)
        for q, gold in r["gold"].items():
            want = max(gold["probabilities"], key=gold["probabilities"].get)
            s = out.setdefault(q, [0, 0, 0.0])
            s[0] += answers[q].get("choice") == want
            s[1] += 1
            s[2] += took
    return {q: (int(a), int(n), t / max(n, 1)) for q, (a, n, t) in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dati", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--nuovo", required=True)
    args = ap.parse_args()
    import laya

    rows = [json.loads(line) for line in open(args.dati) if line.strip()]
    before = score(laya.load(args.base, device="cpu"), rows)
    after = score(laya.load(args.nuovo, device="cpu"), rows)
    print("| domanda | Laya di partenza | Laya addestrato | tempo per domanda (CPU) |")
    print("|---|---|---|---|")
    better = 0
    for q, (ok, n, t) in after.items():
        b = before.get(q, (0, n, 0))
        print(f"| {q} | {b[0]}/{n} ({b[0] / n:.0%}) | {ok}/{n} ({ok / n:.0%}) | {t * 1000:.0f} ms |")
        better += ok > b[0]
    good = better >= len(after) / 2 and sum(a[0] for a in after.values()) > sum(b[0] for b in before.values())
    if not good:
        print("\nL'addestramento non migliora abbastanza: non si pubblica.")
    return 0 if good else 3


if __name__ == "__main__":
    raise SystemExit(main())
