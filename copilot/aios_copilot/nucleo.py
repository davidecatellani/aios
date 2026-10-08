"""Il nucleo: un solo modello piccolo in memoria, con un adattatore (LoRA) per ogni compito.

Invece di tenere caricati più modelli piccoli (Tev1 per l'ambito, Qwen 0.8B per i campi…), SoIA carica una
volta sola Qwen3.5 0.8B e sopra ci mette degli «adattatori»: pochi MB ciascuno, addestrati sulle frasi di
Nova (copilot/addestramento). Il modello base è condiviso; per ogni richiesta si accende l'adattatore giusto:

- smistamento: l'ambito della frase (agenda, posta, file…) e l'azione da fare;
- campi: i valori dell'azione (cosa ricordare, quando…) in JSON;
- documenti: legge bollette, scontrini, avvisi di pagamento (campi in JSON o tutto il testo);
- verifica: il controllo di qualità di una personalizzazione (anteprima.py): dalla richiesta dell'utente e da cosa è
  cambiato nella pagina, misurato (cambiamenti.py), dice se la modifica è riuscita e cosa non torna.

Qwen3.5 vede anche le immagini (con il proiettore mmproj.gguf): il nucleo descrive le foto e legge i
documenti al posto di MiniCPM-V e DeepSeek-OCR, senza un altro modello in memoria.

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
ADAPTERS = ("smistamento", "campi")  # quelli che servono allo smistatore; «documenti» è facoltativo
DOCUMENT_FIELDS = ("tipo", "emittente", "numero", "data", "scadenza", "totale")
DOCUMENT_SCHEMA = {"type": "object", "properties": {k: {"type": "string"} for k in DOCUMENT_FIELDS},
                   "required": list(DOCUMENT_FIELDS)}
SYSTEM_DOCUMENTO = "AIOS · documento"
PROMPT_DOCUMENTO = ("Leggi il documento e rispondi con un JSON: tipo, emittente, numero, data (AAAA-MM-GG), "
                    "scadenza (AAAA-MM-GG o vuota), totale (come scritto, es. 123,45).")
PROMPT_TESTO = "Trascrivi tutto il testo del documento, riga per riga."
SYSTEM_VERIFICA = "AIOS · verifica"
# i problemi che il controllo di qualità sa riconoscere (si sceglie tra questi: un modello piccolo li impara meglio)
VERIFY_PROBLEMS = ("manca quello che è stato chiesto", "colore diverso da quello chiesto", "posizione sbagliata",
                   "dimensione sbagliata", "testo diverso da quello chiesto", "è sparito un elemento che doveva restare",
                   "elemento duplicato", "testi sovrapposti", "testo tagliato", "testo poco leggibile",
                   "elemento fuori dallo schermo", "pagina vuota o rotta")
VERIFY_SCHEMA = {"type": "object", "properties": {
    "fatto": {"type": "boolean"},
    "problemi": {"type": "array", "items": {"type": "string", "enum": list(VERIFY_PROBLEMS)}}},
    "required": ["fatto", "problemi"]}


def prompt_verifica(request: str, changes: list[str]) -> str:
    """La richiesta e cosa è cambiato nella pagina (misurato, cambiamenti.py): la modifica è riuscita?"""
    lines = "\n".join(f"- {c}" for c in changes) or "- niente: la pagina è uguale a prima"
    return (f"Richiesta dell'utente: «{request.strip()[:300]}».\n"
            f"Cosa è cambiato nella pagina (misurato prima e dopo la modifica):\n{lines}\n"
            "La modifica fa quello che l'utente ha chiesto, senza rompere niente? Rispondi con un JSON: fatto (vero o "
            "falso) e problemi. Problemi possibili: " + "; ".join(VERIFY_PROBLEMS) + ".")


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

    def vision(self) -> bool:
        """Il nucleo vede le immagini (llama-server avviato con il proiettore mmproj)?"""
        try:
            return bool(self.get(f"{self.url}/props", 2.0).get("modalities", {}).get("vision"))
        except (OSError, ValueError, AttributeError, urllib.error.URLError):
            return False

    def see(self, image: bytes | list[bytes], question: str, adapter: str | None = None, system: str = "",
            schema: dict[str, Any] | None = None, max_tokens: int = 400, timeout: float = 300.0,
            background: dict[str, Any] | None = None) -> str:
        """Guarda un'immagine (JPEG/PNG; o più di una, in ordine) e risponde. Con `adapter` si accende quell'adattatore.
        Con `background` (argomenti per precedenza.background_chat) è un lavoro di sottofondo che cede il passo
        all'utente e poi riprende."""
        import base64

        def part(img: bytes) -> dict[str, Any]:
            mime = "image/png" if img[:4] == b"\x89PNG" else "image/jpeg"
            return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(img).decode()}"}}

        ids = self.adapters()
        images = image if isinstance(image, list) else [image]
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": [
            *[part(i) for i in images], {"type": "text", "text": question}]}]
        payload: dict[str, Any] = {"messages": messages, "temperature": 0, "max_tokens": max_tokens,
                                   "chat_template_kwargs": {"enable_thinking": False},
                                   "lora": [{"id": i, "scale": 1.0 if name == adapter else 0.0} for name, i in ids.items()]}
        if schema:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"schema": schema}}
        if background is not None and self.post is _post:  # (con un client finto, nelle prove, si chiede e basta)
            from .precedenza import background_chat

            return background_chat(f"{self.url}/v1/chat/completions", payload, timeout,
                                   resume=schema is None, **background).strip()
        reply = self.post(f"{self.url}/v1/chat/completions", payload, timeout)
        return str(reply["choices"][0]["message"].get("content") or "").strip()

    def read_document(self, image: bytes, fields: bool = True) -> str:
        """Legge un documento fotografato: i campi (JSON) o tutto il testo. Usa l'adattatore se c'è."""
        adapter = "documenti" if "documenti" in self.adapters() else None
        if fields:
            return self.see(image, PROMPT_DOCUMENTO, adapter, SYSTEM_DOCUMENTO, DOCUMENT_SCHEMA, 200)
        return self.see(image, PROMPT_TESTO, adapter, SYSTEM_DOCUMENTO, None, 700)

    def check_change(self, changes: list[str], request: str) -> dict[str, Any] | None:
        """Il controllo di qualità di una modifica con l'adattatore «verifica» (None se l'adattatore non c'è).
        Solo testo: la richiesta e cosa è cambiato, misurato."""
        if "verifica" not in self.adapters():
            return None
        try:
            data = json.loads(self.see([], prompt_verifica(request, changes), "verifica", SYSTEM_VERIFICA, VERIFY_SCHEMA, 120))
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            return None
        return data if isinstance(data, dict) else None

    def fill(self, prompt: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any] | None:
        schema = {"type": "object", "properties": properties, "required": required}
        try:
            reply = self._complete("campi", prompt, {"json_schema": schema}, 160)
            args = json.loads(reply.get("content", ""))
        except (OSError, ValueError, urllib.error.URLError):
            return None
        return args if isinstance(args, dict) else None
