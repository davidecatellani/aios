"""Il System One di Nova: un solo modello decisionale per tutte le scelte rapide.

Laya (addestrato per Nova, addestramento/dati_laya.py) risponde in un colpo solo a più domande sulla stessa
frase: di che ambito è, quale azione, che percorso (comando, risposta veloce, ragionamento), se la frase
sentita dal microfono è per Nova, la categoria e l'importanza di una mail, se ricaricare Nova durante un
gioco. Gira come servizio (aios-decisore, laya-serve su 127.0.0.1:11437) con l'API System One:
POST /v1/systemone {"state": testo, "questions": {...}} → {"answers": {...}}.

Se Laya non c'è (immagine senza, servizio fermo) le stesse domande vanno a Tev1 in Ollama, che parla la
stessa API. Se non risponde nessuno: None, e chi chiede usa le sue regole.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

LAYA_URL = "http://127.0.0.1:11437"
LAYA_MODEL = "nova"  # il checkpoint addestrato, come lo chiama aios-decisore
TEV1_MODEL = "tev1:0.8b"
TIMEOUT = 8.0
RETRY_AFTER = 60.0  # dopo un errore si riprova fra un minuto (intanto le regole)


def _post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class Decisore:
    def __init__(self, post: Callable[[str, dict[str, Any], float], dict[str, Any]] | None = None,
                 laya_url: str | None = None, ollama_url: str | None = None, tev1: str | None = None):
        self.post = post or _post
        off = os.environ.get("AIOS_DECISORE", "") == "spento"
        # laya_url="" → senza Laya (prove, o chi vuole solo Tev1)
        self.laya = None if off or laya_url == "" else (laya_url or os.environ.get("AIOS_DECISORE_URL", LAYA_URL)).rstrip("/")
        self.ollama = (ollama_url or os.environ.get("AIOS_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.tev1 = tev1 or os.environ.get("AIOS_SMISTATORE", TEV1_MODEL)
        self._off: dict[str, float] = {}
        self.last_from = ""

    def backends(self) -> list[tuple[str, str]]:
        out = [("laya", f"{self.laya}/v1/systemone")] if self.laya else []
        if self.tev1 != "spento":
            out.append(("tev1", f"{self.ollama}/v1/systemone"))
        return out

    def available(self, only: str | None = None) -> bool:
        return any(time.monotonic() >= self._off.get(name, 0.0) for name, _ in self.backends() if only in (None, name))

    def ask(self, state: str, questions: dict[str, dict[str, Any]], timeout: float = TIMEOUT,
            only: str | None = None) -> dict[str, Any] | None:
        """Le risposte alle domande ({nome: {"choice", "confidence", ...}}), o None se nessuno risponde.
        `only`: solo quel modello ("laya" o "tev1")."""
        if not questions or not state.strip():
            return None
        for name, url in self.backends():
            if only not in (None, name) or time.monotonic() < self._off.get(name, 0.0):
                continue
            payload = {"model": LAYA_MODEL if name == "laya" else self.tev1, "state": state[:4000],
                       "questions": questions}
            try:
                answers = self.post(url, payload, timeout)["answers"]
                if not isinstance(answers, dict):
                    raise ValueError("risposta senza answers")
            except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
                self._off[name] = time.monotonic() + RETRY_AFTER
                continue
            self.last_from = name
            return answers
        return None

    def choose(self, state: str, name: str, question: dict[str, Any], only: str | None = None) -> tuple[str, float] | None:
        """Una domanda a scelta: → (scelta, fiducia) o None."""
        answers = self.ask(state, {name: question}, only=only)
        try:
            a = answers[name] if answers else None
            return (str(a["choice"]), float(a.get("confidence", 0.0))) if a else None
        except (KeyError, TypeError, ValueError):
            return None


_shared: Decisore | None = None


def shared() -> Decisore:
    global _shared
    if _shared is None:
        _shared = Decisore()
    return _shared
