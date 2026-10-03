"""Client per il modello linguistico locale (API di Ollama)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b-instruct"


class LLMError(RuntimeError):
    pass


class ChatModel(Protocol):
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Restituisce il messaggio dell'assistente (eventualmente con tool_calls)."""


class OllamaClient:
    def __init__(self, url: str | None = None, model: str | None = None, timeout: int = 300):
        self.url = (url or os.environ.get("AIOS_OLLAMA_URL", DEFAULT_URL)).rstrip("/")
        self.model = model or os.environ.get("AIOS_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        body = json.dumps(
            {"model": self.model, "messages": messages, "tools": tools, "stream": False}
        ).encode()
        req = urllib.request.Request(
            f"{self.url}/api/chat", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())["message"]
        except urllib.error.HTTPError as exc:
            raise LLMError(f"Il modello ha risposto con errore {exc.code}: {exc.read().decode(errors='replace')}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(
                f"Non riesco a contattare il modello locale su {self.url}. "
                f"Ollama è avviato? (ollama serve; ollama pull {self.model})"
            ) from exc
