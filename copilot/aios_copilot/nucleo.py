"""Il nucleo: un solo modello piccolo in memoria, con un adattatore (LoRA) per ogni compito.

Invece di tenere caricati più modelli piccoli (Tev1 per l'ambito, Qwen 0.8B per i campi…), AIOS carica una
volta sola Qwen3.5 0.8B e sopra ci mette degli «adattatori»: pochi MB ciascuno, addestrati sulle frasi di
Nova (copilot/addestramento). Il modello base è condiviso; per ogni richiesta si accende l'adattatore giusto:

- smistamento: l'ambito della frase (agenda, posta, file…) e l'azione da fare;
- campi: i valori dell'azione (cosa ricordare, quando…) in JSON.

Il servizio è llama-server di llama.cpp (aios-nucleo.service), che tiene più adattatori su un solo modello
e li sceglie richiesta per richiesta. Se il nucleo non c'è (immagine senza adattatori), lo smistatore usa
Tev1 e il modello 0.8B come prima.

Il formato delle richieste (prompt) è definito solo qui: addestramento e uso devono essere identici.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable

URL = os.environ.get("AIOS_NUCLEO_URL", "http://127.0.0.1:11436")
ADAPTERS = ("smistamento", "campi")
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


# --- formato delle richieste (uguale in addestramento e in uso) ----------------------------------------
def chat(system: str, user: str) -> str:
    """Il modello di chat di Qwen, senza ragionamento: la risposta comincia subito."""
    return (f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n\n")


END = "<|im_end|>"


def prompt_ambito(text: str) -> str:
    return chat("AIOS · ambito", text.strip()[:600])


def prompt_azione(text: str, actions: list[str]) -> str:
    return chat("AIOS · azione\nAzioni: " + ", ".join(actions), text.strip()[:600])


def describe_fields(properties: dict[str, Any]) -> str:
    parts = []
    for name, spec in properties.items():
        enum = spec.get("enum")
        parts.append(f"{name} ({spec.get('description', '')}" + (f": {'|'.join(map(str, enum))}" if enum else "") + ")")
    return "; ".join(parts)


def prompt_campi(text: str, tool: str, description: str, properties: dict[str, Any], now: datetime) -> str:
    return chat(f"AIOS · campi\nAzione: {tool}: {description[:160]}\nCampi: {describe_fields(properties)}\n"
                f"Adesso: {GIORNI[now.weekday()]} {now:%Y-%m-%d %H:%M}", text.strip()[:600])


def grammar_choice(options: list[str]) -> str:
    return "root ::= " + " | ".join(json.dumps(o) for o in options)


# --- client ---------------------------------------------------------------------------------------------
def _post(url: str, payload: dict[str, Any], timeout: float) -> Any:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _get(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def _token_prob(entry: dict[str, Any]) -> float:
    """La probabilità (prima della grammatica) del token scelto: vecchio formato «probs», nuovo «logprob»."""
    if "logprob" in entry:
        return math.exp(float(entry["logprob"]))
    if "prob" in entry:
        return float(entry["prob"])
    for alt in entry.get("probs", []):
        if alt.get("tok_str") == entry.get("content"):
            return float(alt.get("prob", 0.0))
    return 0.0


class Nucleo:
    """Il client di aios-nucleo (llama-server con gli adattatori)."""

    def __init__(self, url: str | None = None, post: Callable[[str, dict[str, Any], float], Any] | None = None,
                 get: Callable[[str, float], Any] | None = None, timeout: float = 6.0):
        self.url = (url or URL).rstrip("/")
        self.post = post or _post
        self.get = get or _get
        self.timeout = timeout
        self._ids: dict[str, int] | None = None
        self._off_until = 0.0

    def adapters(self) -> dict[str, int]:
        """{nome dell'adattatore: id} (dal nome del file: smistamento.gguf → smistamento)."""
        if self._ids is None:
            listed = self.get(f"{self.url}/lora-adapters", 2.0)
            self._ids = {os.path.basename(str(a.get("path", ""))).rsplit(".", 1)[0]: int(a["id"]) for a in listed}
        return self._ids

    def available(self) -> bool:
        if time.monotonic() < self._off_until:
            return False
        try:
            return all(a in self.adapters() for a in ADAPTERS)
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
            self._off_until = time.monotonic() + 60
            return False

    def _complete(self, adapter: str, prompt: str, extra: dict[str, Any], n_predict: int) -> dict[str, Any]:
        ids = self.adapters()
        payload = {"prompt": prompt, "n_predict": n_predict, "temperature": 0, "cache_prompt": True, "n_probs": 1,
                   "stop": [END], "lora": [{"id": i, "scale": 1.0 if name == adapter else 0.0} for name, i in ids.items()],
                   **extra}
        try:
            return self.post(f"{self.url}/completion", payload, self.timeout)
        except (OSError, ValueError, urllib.error.URLError):
            self._off_until = time.monotonic() + 60
            raise

    def choose(self, prompt: str, options: list[str], adapter: str = "smistamento") -> tuple[str, float] | None:
        """Una scelta tra opzioni fisse (la grammatica impedisce risposte inventate). → (scelta, fiducia)."""
        try:
            reply = self._complete(adapter, prompt, {"grammar": grammar_choice(options)}, 24)
        except (OSError, ValueError, urllib.error.URLError):
            return None
        choice = str(reply.get("content", "")).strip()
        if choice not in options:
            return None
        probs = [_token_prob(e) for e in reply.get("completion_probabilities") or []]
        return choice, (min(probs) if probs else 0.0)

    def fill(self, prompt: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any] | None:
        schema = {"type": "object", "properties": properties, "required": required}
        try:
            reply = self._complete("campi", prompt, {"json_schema": schema}, 160)
            args = json.loads(reply.get("content", ""))
        except (OSError, ValueError, urllib.error.URLError):
            return None
        return args if isinstance(args, dict) else None
