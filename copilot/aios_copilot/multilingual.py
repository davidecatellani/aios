"""Riconoscimento veloce in tutte le lingue: scelta e calibrazione del modello.

    aios-copilot-setup                      prova i modelli installati e salva il migliore
    aios-copilot-setup --pull               scarica prima i modelli candidati mancanti
    aios-copilot-setup --translate es,fr,de traduce il catalogo con l'LLM locale
    aios-copilot-setup --status             mostra la configurazione attuale

La calibrazione divide le frasi di prova in due metà: sceglie le soglie sulla prima
(nessuna frase fuori tema eseguita, precisione almeno 97%, copertura massima) e
riporta il risultato sulla seconda, che non ha visto. Il numero che conta è quello.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from .evaldata import IN_SCOPE, MULTILINGUAL_IN_SCOPE, MULTILINGUAL_OUT_OF_SCOPE, OUT_OF_SCOPE
from .semantic import CATALOG, IntentSpec, Match, OllamaEncoder, SemanticRouter, tokens, with_examples

# Modelli di embedding multilingue disponibili in Ollama, dal più leggero al più accurato.
CANDIDATES = ["paraphrase-multilingual", "granite-embedding:278m", "bge-m3"]
MIN_PRECISION = 0.97


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios"


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios"


def cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "aios"


@dataclass
class NeuralConfig:
    model: str
    threshold: float
    margin: float
    prefix: str = ""
    # Risultati sulla metà di verifica, per sapere quanto fidarsi.
    precision: float | None = None
    coverage: float | None = None
    false_accepts: int | None = None
    ms_per_query: float | None = None


def load_config(path: Path | None = None) -> NeuralConfig | None:
    path = path or config_dir() / "semantic.json"
    try:
        return NeuralConfig(**json.loads(path.read_text()))
    except (OSError, ValueError, TypeError):
        return None


def save_config(config: NeuralConfig, path: Path | None = None) -> Path:
    path = path or config_dir() / "semantic.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2))
    return path


def load_translations(directory: Path | None = None) -> dict[str, list[str]]:
    """Esempi tradotti: un file catalog-<lingua>.json per lingua, {azione: [frasi]}."""
    merged: dict[str, list[str]] = {}
    for path in sorted((directory or data_dir()).glob("catalog-*.json")):
        try:
            for name, phrases in json.loads(path.read_text()).items():
                merged.setdefault(name, []).extend(p for p in phrases if isinstance(p, str))
        except (OSError, ValueError, AttributeError):
            continue
    return merged


def catalog_with_translations(directory: Path | None = None) -> tuple[IntentSpec, ...]:
    return with_examples(CATALOG, load_translations(directory))


def neural_router(config: NeuralConfig, catalog: Sequence[IntentSpec] | None = None) -> SemanticRouter:
    encoder = OllamaEncoder(
        config.model, threshold=config.threshold, margin=config.margin, prefix=config.prefix, cache_dir=cache_dir()
    )
    return SemanticRouter(catalog=catalog or catalog_with_translations(), encoder=encoder)


# --- Calibrazione -----------------------------------------------------------------

Scored = list[tuple[str, str | None, Match | None]]  # (frase, azione attesa o None se fuori tema, candidato)


def eval_items() -> tuple[list[tuple[str, str | None]], list[tuple[str, str | None]]]:
    """Frasi di prova divise in due metà (calibrazione, verifica), alternandole."""
    items: list[tuple[str, str | None]] = [*IN_SCOPE, *MULTILINGUAL_IN_SCOPE]
    items += [(t, None) for t in [*OUT_OF_SCOPE, *MULTILINGUAL_OUT_OF_SCOPE]]
    return items[0::2], items[1::2]


def without_eval_phrases(catalog: Sequence[IntentSpec]) -> tuple[IntentSpec, ...]:
    """Toglie dal catalogo le frasi di prova (le traduzioni automatiche possono
    coincidere): altrimenti la verifica misurerebbe la memoria, non la comprensione."""
    calibration, validation = eval_items()
    banned = {tuple(tokens(text)) for text, _ in [*calibration, *validation]}
    return tuple(
        replace(spec, examples=tuple(ex for ex in spec.examples if tuple(tokens(ex)) not in banned))
        for spec in catalog
    )


def score_items(router: SemanticRouter, items: Sequence[tuple[str, str | None]]) -> tuple[Scored, float]:
    start = time.perf_counter()
    scored = [(text, expected, router.candidate(text)) for text, expected in items]
    ms = (time.perf_counter() - start) * 1000 / max(len(items), 1)
    return scored, ms


def measure(scored: Scored, threshold: float, margin: float) -> dict[str, Any]:
    in_scope = [(t, e, m) for t, e, m in scored if e is not None]
    correct = wrong = 0
    false_accepts = []
    for text, expected, m in scored:
        accepted = m is not None and SemanticRouter.accept(m, text, threshold, margin)
        if not accepted:
            continue
        if expected is None:
            false_accepts.append(text)
        elif m.spec.name == expected:
            correct += 1
        else:
            wrong += 1
    answered = correct + wrong
    return {
        "precision": correct / answered if answered else 1.0,
        "coverage": answered / len(in_scope) if in_scope else 0.0,
        "false_accepts": false_accepts,
    }


def choose_thresholds(scored: Scored) -> tuple[float, float, dict[str, Any]] | None:
    """Soglie con copertura massima, senza falsi positivi e con precisione ≥ MIN_PRECISION."""
    best: tuple[float, float, dict[str, Any]] | None = None
    for t in range(20, 100):
        for mg in range(0, 21):
            threshold, margin = t / 100, mg / 100
            r = measure(scored, threshold, margin)
            if r["false_accepts"] or r["precision"] < MIN_PRECISION:
                continue
            # A parità di copertura preferisce soglie più alte: più prudenti.
            key = (r["coverage"], threshold, margin)
            if best is None or key > (best[2]["coverage"], best[0], best[1]):
                best = (threshold, margin, r)
    return best


def calibrate(
    make_router: Callable[[], SemanticRouter], model: str, prefix: str = ""
) -> tuple[NeuralConfig, dict[str, Any]] | None:
    router = make_router()
    calibration, validation = eval_items()
    scored_cal, _ = score_items(router, calibration)
    chosen = choose_thresholds(scored_cal)
    if chosen is None:
        return None
    threshold, margin, cal_report = chosen
    scored_val, ms = score_items(router, validation)
    val = measure(scored_val, threshold, margin)
    config = NeuralConfig(
        model=model,
        threshold=threshold,
        margin=margin,
        prefix=prefix,
        precision=round(val["precision"], 3),
        coverage=round(val["coverage"], 3),
        false_accepts=len(val["false_accepts"]),
        ms_per_query=round(ms, 1),
    )
    return config, {"calibration": cal_report, "validation": val}


def is_better(a: NeuralConfig, b: NeuralConfig | None) -> bool:
    """Prima la sicurezza (zero falsi positivi), poi la copertura, poi la velocità."""
    if b is None:
        return True
    key = lambda c: (-(c.false_accepts or 0), (c.precision or 0) >= MIN_PRECISION, c.coverage or 0, -(c.ms_per_query or 0))
    return key(a) > key(b)


# --- Ollama -------------------------------------------------------------------------


def ollama_url() -> str:
    return os.environ.get("AIOS_OLLAMA_URL", "http://localhost:11434").rstrip("/")


def _request(path: str, payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{ollama_url()}{path}", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def installed_models() -> set[str]:
    names = set()
    for m in _request("/api/tags").get("models", []):
        name = m.get("name", "")
        names.add(name)
        if name.endswith(":latest"):
            names.add(name[: -len(":latest")])
    return names


def pull(model: str) -> bool:
    try:
        _request("/api/pull", {"model": model, "stream": False}, timeout=3600)
        return True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"  impossibile scaricare {model}: {exc}")
        return False


# --- Traduzione del catalogo -----------------------------------------------------

LANGUAGE_NAMES = {
    "es": "spagnolo", "fr": "francese", "de": "tedesco", "pt": "portoghese", "nl": "olandese",
    "pl": "polacco", "ru": "russo", "uk": "ucraino", "ro": "rumeno", "el": "greco", "tr": "turco",
    "ar": "arabo", "zh": "cinese semplificato", "ja": "giapponese", "ko": "coreano", "hi": "hindi",
}


def translate_catalog(chat: Callable[[str], str], language: str) -> dict[str, list[str]]:
    """Fa tradurre all'LLM gli esempi di ogni azione, in modo colloquiale."""
    name = LANGUAGE_NAMES.get(language, language)
    result: dict[str, list[str]] = {}
    for spec in CATALOG:
        prompt = (
            f"Traduci in {name} queste richieste rivolte a un computer, come le direbbe una persona "
            f"madrelingua parlando in modo naturale. Una traduzione per riga, senza numeri, "
            f"virgolette o commenti.\n\n" + "\n".join(spec.examples)
        )
        lines = [line.strip(" -•\t\"'") for line in chat(prompt).splitlines()]
        result[spec.name] = [line for line in lines if line and len(line) < 80][: len(spec.examples)]
    return result


def ollama_chat(model: str) -> Callable[[str], str]:
    def chat(prompt: str) -> str:
        reply = _request(
            "/api/chat",
            {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False,
             "options": {"temperature": 0.2}},
            timeout=600,
        )
        return reply["message"]["content"]

    return chat


# --- Comando ------------------------------------------------------------------------


def _print_status() -> int:
    config = load_config()
    translations = sorted(p.stem.removeprefix("catalog-") for p in data_dir().glob("catalog-*.json"))
    if config is None:
        print("Riconoscimento multilingue non configurato: lancia aios-copilot-setup.")
    else:
        print(f"Modello: {config.model} (soglia {config.threshold}, margine {config.margin})")
        print(
            f"Verifica: precisione {config.precision:.0%}, copertura {config.coverage:.0%}, "
            f"falsi positivi {config.false_accepts}, {config.ms_per_query} ms/frase"
        )
    print(f"Cataloghi tradotti: {', '.join(translations) or 'nessuno'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aios-copilot-setup", description="Configura il riconoscimento multilingue")
    parser.add_argument("--models", help=f"modelli da provare, separati da virgola (default: {','.join(CANDIDATES)})")
    parser.add_argument("--pull", action="store_true", help="scarica i modelli candidati mancanti")
    parser.add_argument("--translate", help="lingue in cui tradurre il catalogo, es. es,fr,de")
    parser.add_argument("--llm", default=os.environ.get("AIOS_MODEL", "qwen2.5:7b-instruct"),
                        help="modello Ollama per la traduzione (meglio uno grande: si fa una volta sola)")
    parser.add_argument("--status", action="store_true", help="mostra la configurazione attuale")
    args = parser.parse_args(argv)

    if args.status:
        return _print_status()

    try:
        installed = installed_models()
    except (urllib.error.URLError, OSError) as exc:
        print(f"Ollama non raggiungibile su {ollama_url()} ({exc}). Avvialo con: ollama serve")
        return 1

    if args.translate:
        if args.llm not in installed and not (args.pull and pull(args.llm)):
            print(f"Per tradurre serve il modello {args.llm}: ollama pull {args.llm}")
            return 1
        for lang in [x.strip() for x in args.translate.split(",") if x.strip()]:
            print(f"Traduco il catalogo in {LANGUAGE_NAMES.get(lang, lang)}…")
            path = data_dir() / f"catalog-{lang}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(translate_catalog(ollama_chat(args.llm), lang), ensure_ascii=False, indent=1))
            print(f"  salvato in {path}")

    models = [m.strip() for m in (args.models.split(",") if args.models else CANDIDATES) if m.strip()]
    available = []
    for model in models:
        if model in installed or (args.pull and (print(f"Scarico {model}…") or pull(model))):
            available.append(model)
        else:
            print(f"  {model}: non installato (ollama pull {model}, oppure usa --pull)")
    if not available:
        print("Nessun modello di embedding da provare.")
        return 1

    catalog = without_eval_phrases(catalog_with_translations())
    best: NeuralConfig | None = None
    print(f"\n{'modello':28} {'precisione':>10} {'copertura':>10} {'falsi sì':>9} {'ms/frase':>9}")
    for model in available:
        try:
            outcome = calibrate(lambda: neural_router(NeuralConfig(model, 1.0, 0.0), catalog), model)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
            print(f"{model:28} errore: {exc}")
            continue
        if outcome is None:
            print(f"{model:28} nessuna soglia sicura trovata: scartato")
            continue
        config, _ = outcome
        print(f"{model:28} {config.precision:>10.0%} {config.coverage:>10.0%} {config.false_accepts:>9} {config.ms_per_query:>9}")
        if is_better(config, best):
            best = config

    if best is None:
        print("\nNessun modello è abbastanza affidabile: resta attivo solo il classificatore integrato.")
        return 1
    path = save_config(best)
    print(f"\nScelto {best.model}. Configurazione salvata in {path}.")
    print("Il copilota lo userà dal prossimo avvio per le lingue diverse da italiano e inglese.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
