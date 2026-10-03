"""Client per il modello linguistico locale (API di Ollama)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

DEFAULT_URL = "http://localhost:11434"
# Modello piccolo di default: deve rispondere in fretta anche su CPU senza GPU.
# Sui PC più potenti si può alzare con AIOS_MODEL (es. qwen2.5:7b-instruct).
DEFAULT_MODEL = "qwen2.5:1.5b-instruct"
# Il modello resta in memoria per sempre: niente attese di caricamento tra una richiesta e l'altra.
KEEP_ALIVE = -1


def _configured_text_model() -> str | None:
    from .models import load_config

    return load_config().get("testo")


class LLMError(RuntimeError):
    pass


class ChatModel(Protocol):
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Restituisce il messaggio dell'assistente (eventualmente con tool_calls)."""

    def warmup(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> None:
        """Prepara il modello in anticipo (facoltativo)."""


class LlamaServerClient:
    """Per i modelli a esperti sistemati tra GPU, RAM e disco (moe.py): llama.cpp con API OpenAI."""

    def __init__(self, url: str, model: str, timeout: int = 300):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        from .moe import _post, from_openai, to_openai

        try:
            return from_openai(_post(f"{self.url}/v1/chat/completions",
                                     {"messages": to_openai(messages), "tools": tools or None}, self.timeout))
        except urllib.error.URLError as exc:
            raise LLMError(f"Non riesco a contattare il modello a esperti su {self.url} "
                           "(systemctl --user status aios-esperti).") from exc

    def warmup(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> None:
        pass  # llama-server tiene già il modello caricato


def make_client(model: str | None = None) -> "OllamaClient | LlamaServerClient":
    """Il client giusto per il modello di testo: Ollama, o llama.cpp per un modello a esperti sistemato a pezzi."""
    from .models import load_config

    name = model or os.environ.get("AIOS_MODEL") or _configured_text_model() or DEFAULT_MODEL
    try:
        placed = json.loads(load_config().get("_esperti", "{}")).get(name)
    except ValueError:
        placed = None
    if placed and placed.get("url"):
        return LlamaServerClient(placed["url"], name)
    return OllamaClient(model=name)


class OllamaClient:
    def __init__(self, url: str | None = None, model: str | None = None, timeout: int = 300):
        self.url = (url or os.environ.get("AIOS_OLLAMA_URL", DEFAULT_URL)).rstrip("/")
        # Ordine: scelta esplicita, variabile d'ambiente, modello installato dal consigliere, predefinito.
        self.model = model or os.environ.get("AIOS_MODEL") or _configured_text_model() or DEFAULT_MODEL
        self.timeout = timeout

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        return self._post(
            {"model": self.model, "messages": messages, "tools": tools, "stream": False, "keep_alive": KEEP_ALIVE}
        )["message"]

    def warmup(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> None:
        """Carica il modello e pre-elabora il prompt di sistema e gli strumenti.

        Ollama riusa la cache del prefisso comune, quindi alla prima vera richiesta il
        modello deve leggere solo il messaggio dell'utente: è la parte che su CPU costa di più.
        """
        self._post(
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "keep_alive": KEEP_ALIVE,
                "options": {"num_predict": 1},
            }
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.url}/api/chat", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise LLMError(f"Il modello ha risposto con errore {exc.code}: {exc.read().decode(errors='replace')}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(
                f"Non riesco a contattare il modello locale su {self.url}. "
                f"Ollama è avviato? (ollama serve; ollama pull {self.model})"
            ) from exc
