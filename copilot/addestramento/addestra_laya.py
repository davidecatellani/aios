"""Addestra Laya (decisioni di Nova) sugli esempi di dati_laya.py.

    python addestramento/addestra_laya.py --base laya-multilingual --dati dati-laya/laya.jsonl --uscita laya-nova

Usa l'addestramento ufficiale di Laya (laya.train: perdita sulle distribuzioni, poi la taratura delle
probabilità sulle frasi tenute da parte). Parte dal checkpoint multilingue (mmBERT), che legge l'italiano.
Il risultato si carica con laya.load(cartella) e si serve con laya-serve (aios-decisore).
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="cartella del checkpoint di partenza (laya-multilingual)")
    ap.add_argument("--dati", required=True)
    ap.add_argument("--uscita", required=True)
    ap.add_argument("--epoche", type=int, default=3)
    ap.add_argument("--lotto", type=int, default=8)
    ap.add_argument("--accumula", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=None, help="lunghezza massima (predefinita: quella del checkpoint)")
    args = ap.parse_args()

    from laya.train import TrainConfig, finetune

    config = TrainConfig(epochs=args.epoche, micro_batch=args.lotto, grad_accum=args.accumula, loss="soft-ce",
                         max_len=args.max_len, shuffle_options=("choice",))
    summary = finetune(args.dati, args.base, args.uscita, config, device="auto")
    print(json.dumps(summary, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
