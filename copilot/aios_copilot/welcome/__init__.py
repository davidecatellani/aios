"""Benvenuto di AIOS: il primo avvio è già una conversazione con il copilota.

La pagina (index.html) è servita da un piccolo server locale che la collega
all'agente vero: quello che l'utente scrive durante il benvenuto viene eseguito
davvero, con lo stato in tempo reale e le conferme per le azioni importanti.

Sicurezza: il server ascolta solo su 127.0.0.1 e ogni chiamata alle API deve
portare una chiave casuale generata all'avvio; viene controllato anche l'header
Host, così una pagina web aperta nel browser non può comandare il computer
(nemmeno con il DNS rebinding).
"""

from __future__ import annotations

import hmac
import itertools
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ..agent import Agent
from ..llm import LLMError
from ..status import describe_call
from ..tools import Tool

PAGE = Path(__file__).with_name("index.html")
CONFIRM_TIMEOUT = 300  # secondi: senza risposta l'azione viene annullata
NO_MODEL = (
    "Per le domande libere mi serve il modello AI locale, che non è ancora attivo "
    "(ollama serve). Intanto posso già fare molte cose: prova «alza il volume», "
    "«attiva il tema scuro» o «quanta memoria ho?»."
)


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios"


def profile_path() -> Path:
    return config_dir() / "profile.json"


def load_profile() -> dict[str, Any]:
    try:
        data = json.loads(profile_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_profile(**changes: Any) -> dict[str, Any]:
    profile = {**load_profile(), **changes}
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    return profile


def clean_name(name: Any) -> str:
    """Nome dell'utente: solo lettere, spazi, apostrofi e trattini, max 40 caratteri."""
    if not isinstance(name, str):
        return ""
    return re.sub(r"[^\w\s'’-]|\d|_", "", name).strip()[:40]


@dataclass
class Job:
    """Una richiesta in corso: eventi per la pagina e, se serve, una conferma in sospeso."""

    events: list[dict[str, Any]] = field(default_factory=list)
    answer: str | None = None
    pending: dict[str, Any] | None = None
    _decision: bool = False
    _answered: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, **event: Any) -> None:
        with self._lock:
            self.events.append(event)

    def ask_confirmation(self, tool: Tool, args: dict[str, Any]) -> bool:
        self._answered.clear()
        self.pending = {"label": describe_call(tool, args)}
        answered = self._answered.wait(CONFIRM_TIMEOUT)
        self.pending = None
        return answered and self._decision

    def confirm(self, ok: bool) -> bool:
        if self.pending is None:
            return False
        self._decision = ok
        self._answered.set()
        return True

    def snapshot(self, after: int) -> dict[str, Any]:
        with self._lock:
            return {
                "events": self.events[after:],
                "next": len(self.events),
                "pending": self.pending,
                "answer": self.answer,
                "done": self.answer is not None,
            }


class WelcomeApp:
    """Stato condiviso dal server: agente, richieste in corso, chiave di accesso."""

    def __init__(self, make_agent: Callable[[Callable[[Tool, dict[str, Any]], bool]], Agent]):
        self.token = secrets.token_urlsafe(24)
        self.jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._current: Job | None = None
        # Un solo agente per tutta la conversazione: ricorda il contesto.
        self.agent = make_agent(lambda tool, args: self._current.ask_confirmation(tool, args))
        self._agent_lock = threading.Lock()
        self.finished = threading.Event()

    def start_job(self, text: str) -> str:
        job_id = str(next(self._ids))
        job = self.jobs[job_id] = Job()
        threading.Thread(target=self._run, args=(job, text), daemon=True).start()
        return job_id

    def _run(self, job: Job, text: str) -> None:
        def on_event(kind: str, data: dict[str, Any]) -> None:
            if kind == "routed":
                job.add(kind="fast", level=data["level"])
            elif kind == "tool_call":
                job.add(kind="status", text=describe_call(data["tool"], data["args"]))

        with self._agent_lock:
            self._current = job
            try:
                job.answer = self.agent.ask(text, on_event)
            except LLMError:
                job.answer = NO_MODEL
            except Exception as exc:  # la pagina deve sempre ricevere una risposta
                job.answer = f"Qualcosa è andato storto: {exc}"
            finally:
                self._current = None


def make_handler(app: WelcomeApp, port_ref: list[int]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        # --- risposte -----------------------------------------------------------
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, status: int = 200) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        # --- controlli di sicurezza --------------------------------------------
        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in (f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}")

        def _authorized(self) -> bool:
            token = self.headers.get("X-AIOS-Token", "")
            return self._host_ok() and hmac.compare_digest(token.encode(), app.token.encode())

        def _body(self) -> dict[str, Any]:
            length = min(int(self.headers.get("Content-Length") or 0), 16_384)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
                return data if isinstance(data, dict) else {}
            except ValueError:
                return {}

        # --- rotte --------------------------------------------------------------
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if not self._host_ok():
                return self._json({"error": "host non valido"}, 403)
            if path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if not self._authorized():
                return self._json({"error": "non autorizzato"}, 403)
            if path == "/api/state":
                return self._json({"profile": load_profile()})
            m = re.fullmatch(r"/api/job/(\d+)", path)
            if m and m.group(1) in app.jobs:
                query = urlparse(self.path).query
                after = int(re.search(r"after=(\d+)", query).group(1)) if "after=" in query else 0
                return self._json(app.jobs[m.group(1)].snapshot(after))
            self._json({"error": "non trovato"}, 404)

        def do_POST(self) -> None:
            if not self._authorized():
                return self._json({"error": "non autorizzato"}, 403)
            path = urlparse(self.path).path
            body = self._body()
            if path == "/api/profile":
                changes = {}
                if "name" in body:
                    changes["name"] = clean_name(body["name"])
                if body.get("lang") in ("it", "en"):
                    changes["lang"] = body["lang"]
                return self._json({"profile": save_profile(**changes)})
            if path == "/api/ask":
                text = body.get("text")
                if not isinstance(text, str) or not text.strip() or len(text) > 500:
                    return self._json({"error": "richiesta non valida"}, 400)
                return self._json({"job": app.start_job(text.strip())})
            m = re.fullmatch(r"/api/job/(\d+)/confirm", path)
            if m and m.group(1) in app.jobs:
                return self._json({"ok": app.jobs[m.group(1)].confirm(bool(body.get("ok")))})
            if path == "/api/finish":
                save_profile(welcome_done=True)
                app.finished.set()
                return self._json({"ok": True})
            self._json({"error": "non trovato"}, 404)

    return Handler


def serve(app: WelcomeApp, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    port_ref = [port]
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port_ref))
    port_ref[0] = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # La chiave viaggia nel frammento (#), che il browser non invia mai al server né ai log.
    return server, f"http://127.0.0.1:{port_ref[0]}/#t={app.token}"


def open_window(url: str, finished: threading.Event) -> None:
    """Finestra dedicata con WebKitGTK se disponibile, altrimenti il browser predefinito."""
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("WebKit", "6.0")
        from gi.repository import GLib, Gtk, WebKit

        app = Gtk.Application(application_id="org.aios.Welcome")

        def activate(application: Gtk.Application) -> None:
            window = Gtk.ApplicationWindow(application=application, title="Benvenuto in AIOS")
            window.set_default_size(980, 720)
            view = WebKit.WebView()
            view.load_uri(url)
            window.set_child(view)
            window.present()

            def check() -> bool:
                if finished.is_set():
                    application.quit()
                    return False
                return True

            GLib.timeout_add(300, check)

        app.connect("activate", activate)
        app.run(None)
    except (ImportError, ValueError):
        import webbrowser

        webbrowser.open(url)
        print(f"Benvenuto aperto nel browser: {url.split('#')[0]}  (Ctrl+C per chiudere)")
        try:
            finished.wait()
        except KeyboardInterrupt:
            pass


def main(argv: list[str] | None = None) -> int:
    import argparse

    from ..__main__ import make_agent

    parser = argparse.ArgumentParser(prog="aios-welcome", description="Benvenuto di AIOS")
    parser.add_argument("--first-run", action="store_true", help="non fare nulla se il benvenuto è già stato completato")
    parser.add_argument("--no-window", action="store_true", help="stampa solo l'indirizzo (es. per aprirlo da un altro dispositivo locale)")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)

    if args.first_run and load_profile().get("welcome_done"):
        return 0
    app = WelcomeApp(make_agent)
    server, url = serve(app, args.port)
    try:
        if args.no_window:
            print(url, flush=True)
            app.finished.wait()
        else:
            open_window(url, app.finished)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0
