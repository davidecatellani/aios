"""Delega AI dal telefono al PC.

Il telefono ha un modello piccolo; il PC uno grande. Quando sono vicini:

1. **Cervello del PC per il copilota del telefono** (`/api/modello`): il copilota del
   telefono resta lui, con i suoi strumenti (agenda, SMS, foto del telefono), ma il
   ragionamento lo fa il modello del PC. Se il PC non risponde si torna in un attimo
   al modello del telefono (`HybridModel`). Connessione HTTPS con il certificato del
   PC «fissato» al momento dell'abbinamento: nessun altro può fingersi il PC.
2. **«Chiedi al PC»** (`/api/chiedi`): una domanda dalla pagina del telefono eseguita
   dal copilota del PC, ma solo con strumenti che leggono o rispondono (file, posta,
   agenda, web, consigli); niente che cambi il PC da lontano. Le conferme (es. inviare
   una mail) si danno sul telefono.
"""

from __future__ import annotations

import hashlib
import http.client
import itertools
import json
import os
import ssl
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

# Strumenti che il telefono può far usare al copilota del PC: leggere, cercare, rispondere,
# annotare. Esclusi quelli che cambiano il PC (app, impostazioni, spegnimento, temi,
# riordino, schermo) e quelli del telefono stesso.
PHONE_ALLOWED = frozenset({
    "search_web", "read_webpage", "search_files", "read_file", "system_info",
    "add_reminder", "add_event", "list_agenda", "daily_briefing", "complete_reminder",
    "mail_overview", "search_mail", "read_mail", "send_email",
    "list_collections", "show_collection", "cleanup_suggestions",
    "list_subscriptions", "recommend", "memory_status", "models_status", "suggest_models", "list_themes",
    "show_document", "diet_today", "shopping_list", "energy_status",  # il file trovato arriva sul telefono come pulsante «Apri»
})
MAX_MESSAGES_BYTES = 1_000_000


class Assistant:
    """Le richieste «Chiedi al PC»: un copilota con strumenti limitati, conferme sul telefono."""

    def __init__(self, make_agent: Callable[..., Any], attach: Callable[[Path], str | None] | None = None):
        from ..localapp import Job

        self.attach = attach  # file del PC → link monouso per il telefono
        self._Job = Job
        self.jobs: dict[str, Any] = {}
        self._ids = itertools.count(1)
        self._current = None
        self._lock = threading.Lock()
        self.agent = make_agent(lambda tool, args, **kw: self._current.ask_confirmation(tool, args, **kw))

    def ask(self, text: str) -> str:
        job_id = str(next(self._ids))
        job = self.jobs[job_id] = self._Job()
        threading.Thread(target=self._run, args=(job, text), daemon=True).start()
        return job_id

    def _run(self, job: Any, text: str) -> None:
        from ..llm import LLMError
        from ..status import describe_call

        def on_event(kind: str, data: dict[str, Any]) -> None:
            if kind == "tool_call":
                job.add(kind="status", text=describe_call(data["tool"], data["args"]))

        from ..documents import deliver_to

        def deliver(path: Path) -> str:
            url = self.attach(path) if self.attach else None
            if url is None:
                return f"Il file «{path.name}» non si può aprire dal telefono (è in una cartella privata)."
            job.add(kind="file", name=path.name, url=url)
            return f"Ecco «{path.name}»: toccalo qui sotto per aprirlo."

        with self._lock:
            self._current = job
            deliver_to.set(deliver)
            try:
                job.answer = self.agent.ask(text, on_event, "La domanda arriva dal telefono dell'utente.")
            except LLMError:
                job.answer = "Il modello AI del PC non è attivo in questo momento."
            except Exception as exc:
                job.answer = f"Qualcosa è andato storto: {exc}"
            finally:
                self._current = None


class Brain:
    """Il modello del PC prestato al telefono: una richiesta alla volta."""

    def __init__(self, client_factory: Callable[[], Any] | None = None):
        self.factory = client_factory
        self._client = None
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            from ..llm import make_client

            self._client = (self.factory or make_client)()
        return self._client

    def info(self) -> dict[str, Any]:
        return {"modello": getattr(self.client, "model", ""), "pc": os.uname().nodename}

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            return self.client.chat(messages, tools)


def handle_api(server: Any, method: str, path: str, body: dict[str, Any], query: str) -> tuple[int, Any] | None:
    """Rotte della delega (il dispositivo è già stato autenticato). None = rotta sconosciuta."""
    from ..llm import LLMError

    if method == "GET" and path == "/api/modello":
        if server.brain is None:
            return 404, {"error": "delega non attiva"}
        return 200, server.brain.info()
    if method == "POST" and path == "/api/modello":
        if server.brain is None:
            return 404, {"error": "delega non attiva"}
        messages, tools = body.get("messages"), body.get("tools") or []
        if not isinstance(messages, list) or not isinstance(tools, list):
            return 400, {"error": "richiesta non valida"}
        try:
            return 200, {"message": server.brain.chat(messages, tools)}
        except LLMError as exc:
            return 503, {"error": str(exc)}
    if server.assistant is None:
        return None
    if method == "POST" and path == "/api/chiedi":
        text = body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            return 400, {"error": "richiesta non valida"}
        return 200, {"job": server.assistant.ask(text.strip())}
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[:2] == ["api", "job"] and parts[2] in server.assistant.jobs:
        job = server.assistant.jobs[parts[2]]
        if method == "GET" and len(parts) == 3:
            after = parse_qs(query).get("after", ["0"])[0]
            return 200, job.snapshot(int(after) if after.isdigit() else 0)
        if method == "POST" and parts[3:] == ["conferma"]:
            if type(body.get("ok")) is not bool:
                return 400, {"error": "conferma non valida"}
            confirmation_id = body.get("id")
            if "id" in body and (type(confirmation_id) is not int or confirmation_id <= 0):
                return 400, {"error": "identificativo della conferma non valido"}
            return 200, {"ok": job.confirm(body["ok"], confirmation_id)}
    return None


# --- lato telefono ----------------------------------------------------------------------------------


class PinError(ConnectionError):
    pass


def pc_config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "pc.json"


def load_pc() -> dict[str, str] | None:
    try:
        data = json.loads(pc_config_path().read_text())
        return data if {"url", "key", "fingerprint"} <= data.keys() else None
    except (OSError, ValueError):
        return None


def pinned_request(url: str, method: str, path: str, payload: Any, key: str, fingerprint: str,
                   timeout: float = 300) -> Any:
    """HTTPS verso il PC accettando solo il suo certificato (impronta salvata all'abbinamento)."""
    u = urlparse(url)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # la verifica è l'impronta, qui sotto, prima di inviare qualsiasi dato
    conn = http.client.HTTPSConnection(u.hostname, u.port or 443, context=ctx, timeout=timeout)
    try:
        conn.connect()
        der = conn.sock.getpeercert(binary_form=True)
        if not fingerprint or hashlib.sha256(der).hexdigest() != fingerprint.lower():
            raise PinError("il certificato non è quello del tuo PC")
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = key if key.startswith("AIOS ") else f"Bearer {key}"
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read() or b"{}")
        if resp.status != 200:
            raise ConnectionError(data.get("error", f"errore {resp.status}"))
        return data
    finally:
        conn.close()


def pair_with_pc(pairing_url: str, name: str, request: Callable[..., Any] = pinned_request,
                 kind: str = "telefono") -> dict[str, str]:
    """Dal QR del PC (https://ip:porta/#abbina=codice&fp=impronta) alla chiave del telefono."""
    u = urlparse(pairing_url)
    fragment = dict(p.split("=", 1) for p in u.fragment.split("&") if "=" in p)
    if not fragment.get("abbina") or not fragment.get("fp"):
        raise ValueError("codice QR non valido: rifallo con «collega il telefono» sul PC")
    from .. import identity as ident

    base = f"https://{u.netloc}"
    body = {"code": fragment["abbina"], "name": name, "kind": kind,
            "device_key": ident.b64(ident.device_public_key())}  # per entrare nell'identità dell'utente
    reply = request(base, "POST", "/api/abbina", body, "", fragment["fp"])
    config = {"url": base, "key": reply["key"], "fingerprint": fragment["fp"], "pc": reply.get("pc", "PC")}
    if reply.get("identita"):
        joined = ident.join(reply["identita"])
        peers = [p for p in joined.data.get("pari", []) if p.get("url") != base]
        joined.data["pari"] = peers + [{"url": base, "fingerprint": fragment["fp"], "nome": config["pc"]}]
        joined.save()
        config["identita"] = joined.name
    path = pc_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))
    os.chmod(path, 0o600)
    return config


def signed_requester(identity: Any, url: str, fingerprint: str,
                     request: Callable[..., Any] = pinned_request) -> Callable[[str, str, Any], Any]:
    """Richieste firmate con la chiave di questo dispositivo verso un altro dispositivo dell'utente."""
    def call(method: str, path: str, payload: Any) -> Any:
        body = json.dumps(payload).encode() if payload is not None else b""
        reply = request(url, method, path, payload, identity.sign_request(method, path, body), fingerprint, 60)
        return accept_updates(identity, reply)

    return call


def accept_updates(identity: Any, reply: Any) -> Any:
    """Revoche e nuove chiavi che viaggiano con le risposte (accettate solo se firmate dall'utente e più recenti)."""
    from ..identity import KeyRotated

    if isinstance(reply, dict):
        if isinstance(reply.get("revoche"), dict) and reply["revoche"]:
            identity.accept_revocations(reply["revoche"])
        if isinstance(reply.get("chiavi"), dict) and reply["chiavi"] and identity.accept_keys(reply["chiavi"]):
            raise KeyRotated()
    return reply


def _sync_rotating(identity: Any, engine: Any, peer: str, requester: Callable[[str, str, Any], Any]) -> tuple[int, int]:
    """Una sincronizzazione; se a metà arriva una nuova chiave, si ricifra e si riparte una volta."""
    from ..identity import KeyRotated

    for attempt in range(2):
        if identity.sync_key() and identity.sync_key() != engine.key:
            engine.rekey(identity.sync_key())
        try:
            return engine.sync_with(peer, requester)
        except KeyRotated:
            if attempt:
                raise
    return 0, 0


def sync_peers(identity: Any, engine: Any, request: Callable[..., Any] = pinned_request) -> list[str]:
    """Sincronizza con i dispositivi conosciuti raggiungibili. → righe di riepilogo."""
    report = []
    for peer in identity.data.get("pari", []):
        try:
            got, sent = _sync_rotating(identity, engine, peer["url"],
                                       signed_requester(identity, peer["url"], peer["fingerprint"], request))
            report.append(f"{peer.get('nome', peer['url'])}: ricevute {got}, inviate {sent}")
        except PinError:
            report.append(f"{peer.get('nome', peer['url'])}: certificato diverso, sincronizzazione bloccata per sicurezza")
        except ConnectionError as exc:
            if "riconosciuto" in str(exc):
                report.append(f"{peer.get('nome', peer['url'])}: non riconosce più questo dispositivo (revocato?)")
            else:
                report.append(f"{peer.get('nome', peer['url'])}: non raggiungibile")
        except (OSError, ValueError, KeyError):
            report.append(f"{peer.get('nome', peer['url'])}: non raggiungibile")
    relay = identity.data.get("relay")
    if relay:  # anche fuori casa, tramite il relay cifrato
        from ..relay import relay_requester

        try:
            got, sent = _sync_rotating(identity, engine, f"relay:{relay['url']}", relay_requester(identity, relay))
            report.append(f"relay: ricevute {got}, inviate {sent}")
        except ConnectionError as exc:
            report.append("relay: " + ("non riconosce più questo dispositivo (revocato?)" if "riconosciuto" in str(exc)
                                       else f"non disponibile ({exc})"))
        except (OSError, ValueError, KeyError) as exc:
            report.append(f"relay: non raggiungibile ({exc})")
    return report


class RemoteBrain:
    """Il modello del PC visto dal telefono (stessa interfaccia del client locale)."""

    def __init__(self, config: dict[str, str], request: Callable[..., Any] = pinned_request):
        self.config, self.request = config, request
        self.model = f"PC ({config.get('pc', '')})"

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if len(json.dumps(messages)) > MAX_MESSAGES_BYTES:
            messages = messages[:1] + messages[-20:]
        c = self.config
        return self.request(c["url"], "POST", "/api/modello", {"messages": messages, "tools": tools},
                            c["key"], c["fingerprint"])["message"]

    def reachable(self) -> bool:
        c = self.config
        try:
            self.request(c["url"], "GET", "/api/modello", None, c["key"], c["fingerprint"], 2)
            return True
        except PinError:
            raise
        except (OSError, ValueError, ConnectionError):
            return False

    def warmup(self, *a: Any) -> None:
        pass


class HybridModel:
    """Sul telefono: il modello del PC quando è vicino, altrimenti quello del telefono.

    Se il PC non risponde si passa subito al modello locale e lo si riprova dopo un po'.
    """

    RETRY = 30.0

    def __init__(self, remote: RemoteBrain, local: Any, clock: Callable[[], float] = time.monotonic):
        self.remote, self.local, self.clock = remote, local, clock
        self.down_until = 0.0
        self.last = ""

    @property
    def model(self) -> str:
        return self.last or getattr(self.local, "model", "")

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if self.clock() >= self.down_until:
            try:
                reply = self.remote.chat(messages, tools)
                self.last = self.remote.model
                return reply
            except PinError:
                self.down_until = self.clock() + 3600  # certificato diverso: mai mandare dati a chi non è il PC
            except (OSError, ConnectionError, ValueError, KeyError):
                self.down_until = self.clock() + self.RETRY
        self.last = getattr(self.local, "model", "telefono")
        return self.local.chat(messages, tools)

    def warmup(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> None:
        try:
            self.local.warmup(messages, tools)
        except Exception:
            pass
