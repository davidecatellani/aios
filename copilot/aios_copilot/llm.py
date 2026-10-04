"""Client per il modello linguistico locale (API di Ollama)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Any, Protocol

DEFAULT_URL = "http://localhost:11434"
# Modello piccolo di default: deve rispondere in fretta anche su CPU senza GPU.
# Sui PC più potenti si può alzare con AIOS_MODEL (es. qwen2.5:7b-instruct).
DEFAULT_MODEL = "qwen3.5:2b"
# Modelli che «pensano» prima di rispondere: per Nova serve una risposta pronta, il ragionamento
# lungo su CPU costerebbe decine di secondi.
THINKING_PREFIXES = ("qwen3.5", "qwen3.6", "gemma4")
# Il modello resta in memoria per sempre: niente attese di caricamento tra una richiesta e l'altra.
KEEP_ALIVE = -1


def _configured_text_model() -> str | None:
    from .models import load_config

    return load_config().get("testo")


def start_pull(model: str) -> bool:
    """Scarica in sottofondo un modello mancante (una volta sola per volta). → True se partito."""
    import shutil
    import subprocess
    import tempfile

    exe = shutil.which("ollama")
    if not exe:
        return False
    flag = Path(tempfile.gettempdir()) / f"aios-pull-{re.sub(r'[^a-z0-9._-]', '_', model.lower())}"
    if flag.exists() and time.time() - flag.stat().st_mtime < 3600:
        return True  # già in corso
    flag.touch()
    subprocess.Popen([exe, "pull", model], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True


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


def make_client(model: str | None = None) -> Any:
    """Il client giusto per il modello di testo: Ollama, o llama.cpp per un modello a esperti sistemato a pezzi."""
    from .models import load_config

    name = model or os.environ.get("AIOS_MODEL") or _configured_text_model() or DEFAULT_MODEL
    try:
        placed = json.loads(load_config().get("_esperti", "{}")).get(name)
    except ValueError:
        placed = None
    local = LlamaServerClient(placed["url"], name) if placed and placed.get("url") else OllamaClient(model=name)
    from .mesh.delegate import HybridModel, RemoteBrain, load_pc

    pc = load_pc()  # su un telefono abbinato a un PC: il modello del PC quando è vicino
    return HybridModel(RemoteBrain(pc), local) if pc and not model else local


class OllamaClient:
    def __init__(self, url: str | None = None, model: str | None = None, timeout: int = 300):
        self.url = (url or os.environ.get("AIOS_OLLAMA_URL", DEFAULT_URL)).rstrip("/")
        # Ordine: scelta esplicita, variabile d'ambiente, modello installato dal consigliere, predefinito.
        self.model = model or os.environ.get("AIOS_MODEL") or _configured_text_model() or DEFAULT_MODEL
        self.timeout = timeout

    supports_stream = True

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
             on_token: Callable[[str], None] | None = None) -> dict[str, Any]:
        if on_token is None:
            return self._post(self._payload(messages, tools))["message"]
        return self._stream(self._payload(messages, tools, stream=True), on_token)

    def _stream(self, payload: dict[str, Any], on_token: Callable[[str], None]) -> dict[str, Any]:
        """La risposta parola per parola: su un processore lento la prima parola arriva in pochi secondi."""
        body = json.dumps(payload).encode()
        req = urllib.request.Request(f"{self.url}/api/chat", data=body, headers={"Content-Type": "application/json"})
        content, calls = [], []
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    msg = chunk.get("message") or {}
                    if msg.get("tool_calls"):
                        calls.extend(msg["tool_calls"])
                    piece = msg.get("content") or ""
                    if piece:
                        content.append(piece)
                        if not calls:
                            on_token(piece)
                    if chunk.get("done"):
                        break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # modello assente: stesso messaggio della richiesta normale
                return self._post({**payload, "stream": False})["message"]
            raise LLMError(f"Il modello ha risposto con errore {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"Non riesco a contattare il modello: {exc.reason}") from exc
        message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
        if calls:
            message["tool_calls"] = calls
        return message

    def _payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        payload = {"model": self.model, "messages": messages, "tools": tools, "stream": False,
                   "keep_alive": KEEP_ALIVE, **extra}
        if self.model.startswith(THINKING_PREFIXES):
            payload["think"] = False
        return payload

    def warmup(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> None:
        """Carica il modello e pre-elabora il prompt di sistema e gli strumenti.

        Ollama riusa la cache del prefisso comune, quindi alla prima vera richiesta il
        modello deve leggere solo il messaggio dell'utente: è la parte che su CPU costa di più.
        """
        self._post(self._payload(messages, tools, options={"num_predict": 1}))

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.url}/api/chat", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            if exc.code == 404 and "not found" in detail:
                started = start_pull(self.model)
                raise LLMError(
                    "Il mio modello AI non è ancora sul computer"
                    + (": lo sto scaricando (circa 3 GB, serve internet). " if started else ". ")
                    + "Intanto capisco i comandi semplici, come «alza il volume» o «che ore sono»."
                ) from exc
            raise LLMError(f"Il modello ha risposto con errore {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(
                f"Non riesco a contattare il modello locale su {self.url}. "
                f"Ollama è avviato? (ollama serve; ollama pull {self.model})"
            ) from exc
