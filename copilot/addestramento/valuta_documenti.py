"""Misura la lettura dei documenti, con aios-nucleo (llama-server con mmproj e adattatori) già avviato.

    python addestramento/valuta_documenti.py --dati dati-documenti --url http://127.0.0.1:11436

Campi: quanti campi giusti (uguali a quelli veri). Testo: somiglianza della trascrizione (0–100%).
Confronta il modello senza adattatore con l'adattatore «documenti»; esce una tabella in Markdown.
Codice d'uscita 3 se l'adattatore non migliora i campi (allora non si pubblica).
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aios_copilot.nucleo import (DOCUMENT_FIELDS, DOCUMENT_SCHEMA, SYSTEM_DOCUMENTO, Nucleo)  # noqa: E402


def norm(text: str) -> str:
    return " ".join(text.split()).lower()


def run(n: Nucleo, base: Path, rows: list[dict], adapter: str | None) -> dict[str, float]:
    fields_ok = fields_all = docs_ok = docs = 0
    sim = 0.0
    texts = 0
    took = 0.0
    for r in rows:
        image = (base / r["immagine"]).read_bytes()
        t = time.monotonic()
        if r["compito"] == "documento":
            got = n.see(image, r["prompt"], adapter, SYSTEM_DOCUMENTO, DOCUMENT_SCHEMA, 200)
            try:
                data = json.loads(got)
            except ValueError:
                data = {}
            want = json.loads(r["risposta"])
            right = sum(norm(str(data.get(k, ""))) == norm(want[k]) for k in DOCUMENT_FIELDS)
            fields_ok += right
            fields_all += len(DOCUMENT_FIELDS)
            docs_ok += right == len(DOCUMENT_FIELDS)
            docs += 1
        else:
            got = n.see(image, r["prompt"], adapter, SYSTEM_DOCUMENTO, None, 700)
            sim += difflib.SequenceMatcher(None, norm(got), norm(r["risposta"])).ratio()
            texts += 1
        took += time.monotonic() - t
    return {"campi": fields_ok / max(fields_all, 1), "documenti": docs_ok / max(docs, 1),
            "testo": sim / max(texts, 1), "tempo": took / max(len(rows), 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dati", default="dati-documenti")
    ap.add_argument("--url", default="http://127.0.0.1:11436")
    args = ap.parse_args()
    base = Path(args.dati)
    rows = [json.loads(line) for line in (base / "prova-documenti.jsonl").read_text().splitlines() if line.strip()]
    n = Nucleo(args.url, timeout=300)
    before = run(n, base, rows, None)
    after = run(n, base, rows, "documenti")
    print("| misura | senza adattatore | con adattatore |")
    print("|---|---|---|")
    print(f"| campi giusti | {before['campi']:.0%} | {after['campi']:.0%} |")
    print(f"| documenti con tutti i campi giusti | {before['documenti']:.0%} | {after['documenti']:.0%} |")
    print(f"| somiglianza del testo trascritto | {before['testo']:.0%} | {after['testo']:.0%} |")
    print(f"| tempo medio per richiesta | {before['tempo']:.1f} s | {after['tempo']:.1f} s |")
    good = after["campi"] > before["campi"] and after["testo"] >= before["testo"] - 0.03
    if not good:
        print("\nL'adattatore documenti non migliora abbastanza: non si pubblica.")
    return 0 if good else 3


if __name__ == "__main__":
    raise SystemExit(main())
