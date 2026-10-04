"""Misura gli adattatori su frasi mai viste, con aios-nucleo (llama-server) già avviato.

    python addestramento/valuta.py --dati dati --url http://127.0.0.1:11436

Confronta il modello base senza adattatori con il modello con l'adattatore giusto acceso: esce una
tabella in Markdown (finisce nelle note della pubblicazione degli adattatori).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aios_copilot.nucleo import ADAPTERS, Nucleo, grammar_choice  # noqa: E402


def run(n: Nucleo, rows: list[dict], adapter: str | None) -> dict[str, tuple[int, int, float]]:
    ids = n.adapters()
    lora = [{"id": i, "scale": 1.0 if name == adapter else 0.0} for name, i in ids.items()]
    score: dict[str, list[float]] = {}
    for r in rows:
        extra = {"grammar": grammar_choice(r["opzioni"])} if r["opzioni"] else {}
        if r["compito"] == "campi":
            extra = {"json_schema": {"type": "object"}}
        t = time.monotonic()
        reply = n.post(f"{n.url}/completion", {"prompt": r["prompt"], "n_predict": 160, "temperature": 0,
                                               "cache_prompt": True, "stop": ["<|im_end|>"], "lora": lora, **extra}, 60)
        took = time.monotonic() - t
        got = str(reply.get("content", "")).strip()
        if r["compito"] == "campi":
            try:
                ok = json.loads(got) == json.loads(r["risposta"])
            except ValueError:
                ok = False
        else:
            ok = got == r["risposta"]
        s = score.setdefault(r["compito"], [0, 0, 0.0])
        s[0] += ok
        s[1] += 1
        s[2] += took
    return {k: (int(v[0]), int(v[1]), v[2] / max(v[1], 1)) for k, v in score.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dati", default="dati")
    ap.add_argument("--url", default="http://127.0.0.1:11436")
    args = ap.parse_args()
    n = Nucleo(args.url, timeout=60)
    print("| compito | senza adattatore | con adattatore | tempo medio |")
    print("|---|---|---|---|")
    for adapter in ADAPTERS:
        rows = [json.loads(line) for line in (Path(args.dati) / f"prova-{adapter}.jsonl").read_text().splitlines()]
        before = run(n, rows, None)
        after = run(n, rows, adapter)
        for task, (ok, tot, sec) in after.items():
            b = before.get(task, (0, tot, 0))
            print(f"| {task} | {b[0]}/{tot} ({100 * b[0] / tot:.0f}%) | {ok}/{tot} ({100 * ok / tot:.0f}%) | {sec:.2f} s |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
