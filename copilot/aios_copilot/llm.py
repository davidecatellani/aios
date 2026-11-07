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
# Modello di default: Qwen3.5 4B, il più piccolo che capisce davvero le richieste in italiano (il 2B
# fraintendeva troppo); gira anche su CPU senza GPU. Si cambia con AIOS_MODEL o dalle impostazioni.
DEFAULT_MODEL = "qwen3.5:4b"
OLD_DEFAULTS = {"qwen3.5:2b"}  # i vecchi predefiniti salvati nella configurazione passano al nuovo
# Modelli che «pensano» prima di rispondere: per Nova serve una risposta pronta, il ragionamento
# lungo su CPU costerebbe decine di secondi.
THINKING_PREFIXES = ("qwen3.5", "qwen3.6", "gemma4")


def _keep_alive() -> int | str:
    """Il modello resta caricato (niente attese tra una richiesta e l'altra). Se la memoria serve ad altro lo
    scarica SoIA stesso (giochi.GameMode: giochi e memoria quasi finita), non un timer."""
    return os.environ.get("AIOS_KEEP_ALIVE", -1)


KEEP_ALIVE = _keep_alive()
NUM_CTX = int(os.environ.get("AIOS_NUM_CTX", "8192"))


def _configured_text_model() -> str | None:
    from .models import load_config

    name = load_config().get("testo")
    return None if name in OLD_DEFAULTS else name


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
    """llama.cpp con API OpenAI: i modelli a esperti sistemati tra GPU, RAM e disco (moe.py) e il modello di Nova.

    `slot`: il posto fisso della conversazione in llama-server. Le istruzioni e gli strumenti già letti restano lì e
    la domanda dopo legge solo le parole nuove (prova velocita-1: 1,3 s invece di 33). I lavori di sottofondo usano
    altri posti e non la cancellano. La risposta arriva parola per parola (on_token), come con Ollama."""

    supports_stream = True

    def __init__(self, url: str, model: str, timeout: int = 300, slot: int | None = None):
        self.url, self.model, self.timeout, self.slot = url.rstrip("/"), model, timeout, slot
        self.think = False

    def _payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        from .moe import to_openai

        payload: dict[str, Any] = {"messages": to_openai(messages), "tools": tools or None, "cache_prompt": True,
                                   "chat_template_kwargs": {"enable_thinking": bool(self.think)}, **extra}
        if self.slot is not None:
            payload["id_slot"] = self.slot
        return {k: v for k, v in payload.items() if v is not None}

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
             on_token: Callable[[str], None] | None = None) -> dict[str, Any]:
        from .moe import _post, from_openai

        try:
            if on_token is not None:
                return self._stream(self._payload(messages, tools, stream=True), on_token)
            return from_openai(_post(f"{self.url}/v1/chat/completions", self._payload(messages, tools), self.timeout))
        except urllib.error.URLError as exc:
            raise LLMError(f"Non riesco a contattare il modello su {self.url}.") from exc

    def _stream(self, payload: dict[str, Any], on_token: Callable[[str], None]) -> dict[str, Any]:
        """La risposta a pezzi (SSE): il testo va subito a on_token; le chiamate agli strumenti arrivano a frammenti
        (nome, poi gli argomenti come testo) e si ricompongono alla fine."""
        req = urllib.request.Request(f"{self.url}/v1/chat/completions", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        content: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            if resp.headers.get_content_type() != "text/event-stream":  # un server che risponde tutto insieme
                from .moe import from_openai

                message = from_openai(json.loads(resp.read()))
                if message.get("content") and not message.get("tool_calls"):
                    on_token(message["content"])
                return message
            for raw in resp:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:") or line.endswith("[DONE]"):
                    continue
                try:
                    delta = json.loads(line[5:])["choices"][0].get("delta") or {}
                except (ValueError, KeyError, IndexError):
                    continue
                for c in delta.get("tool_calls") or []:
                    slot = calls.setdefault(int(c.get("index", 0)), {"name": "", "arguments": ""})
                    fn = c.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") or ""
                piece = delta.get("content") or ""
                if piece:
                    content.append(piece)
                    if not calls:
                        on_token(piece)
        message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
        if calls:
            out = []
            for _, c in sorted(calls.items()):
                try:
                    args = json.loads(c["arguments"] or "{}")
                except ValueError:
                    args = {}
                out.append({"function": {"name": c["name"], "arguments": args}})
            message["tool_calls"] = out
        return message

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
        # True per una richiesta che chiede di ragionare (il percorso deciso da Laya): il modello pensa
        # prima di rispondere. Di solito False: risposta pronta.
        self.think = False

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
        # Ollama di base tiene 4096 token: con istruzioni, strumenti e qualche scambio si supera, e allora taglia
        # proprio l'inizio (le istruzioni di Nova). 8192 bastano a una conversazione lunga (~300 MB in più).
        payload["options"] = {"num_ctx": NUM_CTX, **payload.get("options", {})}
        if self.model.startswith(THINKING_PREFIXES):
            payload["think"] = bool(self.think)
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
