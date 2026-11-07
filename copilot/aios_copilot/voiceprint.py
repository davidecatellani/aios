"""L'impronta della voce: Nova risponde solo alle persone che conosce (se l'utente lo vuole).

Vosk ha un piccolo modello che da ogni frase ricava un vettore (x-vector, 128 numeri) che descrive
chi parla. Nel benvenuto (o in Impostazioni › Voce di Nova) l'utente legge alcune frasi: la media dei
vettori è la sua impronta, e la soglia si tara su quanto si somigliano tra loro le sue frasi. Dopo, ogni
richiesta a voce viene confrontata con le impronte salvate: se nessuna corrisponde, Nova non risponde
(un attore del film, la TV, un ospite). Tutto resta sul computer, in ~/.config/aios/voci.json.

Insieme alla cancellazione dell'eco (pipewire.conf.d/50-aios-microfono.conf), che toglie dal microfono
quello che suonano gli altoparlanti, Nova non si fa confondere dai rumori di casa.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Iterable

# Frasi da leggere per imparare la voce: varie nei suoni, senza «Nova» (che attiverebbe l'ascolto).
PHRASES = [
    "Oggi il cielo è sereno e c'è un leggero vento da nord.",
    "Mi ricordi di comprare il pane, il latte e qualche mela?",
    "Domani alle nove ho una riunione importante in ufficio.",
    "Metti un po' di musica tranquilla mentre lavoro.",
    "Quanto manca alla fine della settimana?",
]
MIN_SAMPLES = 3
DEFAULT_THRESHOLD = 0.5


def store_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "voci.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(store_path().read_text())
        if isinstance(data, dict):
            data.setdefault("persone", [])
            data.setdefault("solo_conosciute", bool(data["persone"]))
            return data
    except (OSError, ValueError):
        pass
    return {"persone": [], "solo_conosciute": False}


def save(data: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))
    path.chmod(0o600)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def mean(vectors: list[list[float]]) -> list[float]:
    return [sum(col) / len(vectors) for col in zip(*vectors)]


def enroll(name: str, samples: list[list[float]]) -> dict[str, Any]:
    """Salva (o sostituisce) l'impronta di una persona. Soglia tarata sulle sue stesse frasi."""
    samples = [s for s in samples if s]
    if len(samples) < MIN_SAMPLES:
        raise ValueError(f"servono almeno {MIN_SAMPLES} frasi")
    print_ = mean(samples)
    own = sorted(cosine(s, print_) for s in samples)
    # la frase meno somigliante, con un margine: le richieste reali sono più brevi e varie
    threshold = max(0.3, min(0.7, own[0] - 0.12))
    data = load()
    data["persone"] = [p for p in data["persone"] if p["nome"].lower() != name.lower()]
    data["persone"].append({"nome": name, "impronta": print_, "soglia": round(threshold, 3), "campioni": len(samples)})
    data["solo_conosciute"] = True
    save(data)
    return {"nome": name, "soglia": threshold}


def forget(name: str) -> bool:
    data = load()
    before = len(data["persone"])
    data["persone"] = [p for p in data["persone"] if p["nome"].lower() != name.lower()]
    if not data["persone"]:
        data["solo_conosciute"] = False
    save(data)
    return len(data["persone"]) < before


def set_only_known(on: bool) -> None:
    data = load()
    data["solo_conosciute"] = bool(on and data["persone"])
    save(data)


def who(vector: list[float] | None, data: dict[str, Any] | None = None) -> tuple[str | None, float]:
    """La persona che ha parlato (o None) e quanto le somiglia."""
    data = data or load()
    if not vector:
        return None, 0.0
    best, score = None, 0.0
    for p in data["persone"]:
        s = cosine(vector, p["impronta"])
        if s > score:
            best, score = p, s
    if best is not None and score >= best.get("soglia", DEFAULT_THRESHOLD):
        return best["nome"], score
    return None, score


def accepted(vector: list[float] | None, data: dict[str, Any] | None = None) -> bool:
    """Nova deve rispondere? Sì se non c'è il filtro, o se la voce è di una persona conosciuta.
    Senza vettore (frase troppo corta per riconoscerla) si risponde: meglio che ignorare il padrone."""
    data = data or load()
    if not data.get("solo_conosciute") or not data["persone"] or not vector:
        return True
    return who(vector, data)[0] is not None


def spk_model_dir(dirs: Iterable[Path] | None = None) -> Path | None:
    from .voice import voice_dirs

    for base in dirs if dirs is not None else voice_dirs():
        found = sorted(p for p in Path(base).glob("vosk-model-spk*") if p.is_dir())
        if found:
            return found[0]
    return None


def embed(pcm: bytes, rate: int = 16000) -> list[float] | None:
    """L'x-vector di un pezzo di audio (16 bit mono) con il modello di Vosk."""
    spk = spk_model_dir()
    if spk is None:
        return None
    from vosk import KaldiRecognizer, Model, SpkModel  # type: ignore

    from .voice import find_model

    asr = find_model("vosk")
    if asr is None:
        return None
    rec = KaldiRecognizer(Model(str(asr)), rate)
    rec.SetSpkModel(SpkModel(str(spk)))
    for i in range(0, len(pcm), 4000):
        rec.AcceptWaveform(pcm[i:i + 4000])
    return json.loads(rec.FinalResult()).get("spk")
