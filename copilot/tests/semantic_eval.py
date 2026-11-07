"""Banco di prova del livello 1, con frasi che NON compaiono nel catalogo.

    python tests/semantic_eval.py      stampa precisione, copertura e latenza
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aios_copilot.evaldata import IN_SCOPE, OUT_OF_SCOPE  # noqa: E402


def evaluate(router):
    import time

    correct = wrong = abstained = 0
    errors = []
    start = time.perf_counter()
    for text, expected in IN_SCOPE:
        intent = router.match(text)
        if intent is None:
            abstained += 1
            errors.append(("astenuto", text, expected))
            continue
        spec = next(s for s in router.catalog if s.name == expected).build()
        if intent == spec:
            correct += 1
        else:
            wrong += 1
            errors.append(("SBAGLIATO", text, f"{expected} → {intent}"))
    false_accepts = [t for t in OUT_OF_SCOPE if router.match(t) is not None]
    elapsed = time.perf_counter() - start
    answered = correct + wrong
    return {
        "precision": correct / answered if answered else 1.0,
        "coverage": answered / len(IN_SCOPE),
        "false_accepts": false_accepts,
        "ms_per_query": elapsed * 1000 / (len(IN_SCOPE) + len(OUT_OF_SCOPE)),
        "errors": errors,
    }


if __name__ == "__main__":
    from aios_copilot.semantic import default_router

    r = evaluate(default_router())
    for kind, text, detail in r["errors"]:
        print(f"  {kind:9} «{text}»  ({detail})")
    for text in r["false_accepts"]:
        print(f"  FALSO SÌ  «{text}»")
    print(
        f"\nprecisione {r['precision']:.0%} · copertura {r['coverage']:.0%} · "
        f"falsi positivi {len(r['false_accepts'])}/{len(OUT_OF_SCOPE)} · {r['ms_per_query']:.2f} ms/frase"
    )
