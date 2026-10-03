"""Base comune delle app di sistema con interfaccia web locale (benvenuto, posta…).

Sicurezza: il server ascolta solo su 127.0.0.1; ogni chiamata alle API deve portare
una chiave casuale generata all'avvio (passata nel frammento dell'URL, che il
browser non invia mai a nessuno); l'header Host è controllato contro il DNS
rebinding. Così una pagina web aperta nel browser non può comandare il computer.

Le richieste al copilota girano in background ("job"): la pagina ne segue lo stato e
risponde alle conferme delle azioni importanti.
"""

from __future__ import annotations

import hmac
import itertools
import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .agent import Agent
from .llm import LLMError
from .status import describe_call
from .tools import Tool

CONFIRM_TIMEOUT = 300  # secondi: senza risposta l'azione viene annullata
NO_MODEL = (
    "Per le domande libere mi serve il modello AI locale, che non è ancora attivo "
    "(ollama serve). Intanto posso già fare molte cose: prova «alza il volume», "
    "«attiva il tema scuro» o «quanta memoria ho?»."
)
MAX_BODY = 256 * 1024

Response = tuple[int, Any]  # (stato HTTP, contenuto JSON)


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

    def ask_confirmation(self, tool: Tool, args: dict[str, Any], warning: str | None = None) -> bool:
        self._answered.clear()
        self.pending = {"label": describe_call(tool, args), "warning": warning}
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
            return {"events": self.events[after:], "next": len(self.events), "pending": self.pending,
                    "answer": self.answer, "done": self.answer is not None}


class LocalApp:
    """Stato condiviso dal server: agente, richieste in corso, chiave di accesso, rotte."""

    page: Path

    def __init__(self, make_agent: Callable[[Callable[..., bool]], Agent] | None = None):
        self.token = secrets.token_urlsafe(24)
        self.jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._current: Job | None = None
        self._agent_lock = threading.Lock()
        self.finished = threading.Event()
        # Un solo agente per tutta la sessione: ricorda il contesto.
        self.agent = make_agent(lambda tool, args, **kw: self._current.ask_confirmation(tool, args, **kw)) \
            if make_agent else None
        self.routes: dict[str, list[tuple[re.Pattern[str], Callable[..., Response]]]] = {"GET": [], "POST": []}
        self.route("GET", r"/api/job/(\d+)", self._job_status)
        self.route("POST", r"/api/job/(\d+)/confirm", self._job_confirm)
        self.route("POST", r"/api/ask", self._ask)

    def route(self, method: str, pattern: str, handler: Callable[..., Response]) -> None:
        self.routes[method].append((re.compile(pattern), handler))

    # --- richieste al copilota -------------------------------------------------------
    def start_job(self, text: str, context: str | None = None) -> str:
        job_id = str(next(self._ids))
        job = self.jobs[job_id] = Job()
        threading.Thread(target=self._run, args=(job, text, context), daemon=True).start()
        return job_id

    def _run(self, job: Job, text: str, context: str | None = None) -> None:
        def on_event(kind: str, data: dict[str, Any]) -> None:
            if kind == "routed":
                job.add(kind="fast", level=data["level"])
            elif kind == "tool_call":
                job.add(kind="status", text=describe_call(data["tool"], data["args"]))

        with self._agent_lock:
            self._current = job
            try:
                job.answer = self.agent.ask(text, on_event, context)
            except LLMError:
                job.answer = NO_MODEL
            except Exception as exc:  # la pagina deve sempre ricevere una risposta
                job.answer = f"Qualcosa è andato storto: {exc}"
            finally:
                self._current = None

    def prompt_for(self, body: dict[str, Any]) -> tuple[str, str | None]:
        """Frase da passare al copilota e contesto per il modello (le app lo specializzano)."""
        return body["text"].strip(), None

    def _ask(self, match: re.Match[str], body: dict[str, Any], query: dict[str, str]) -> Response:
        text = body.get("text")
        if self.agent is None:
            return 404, {"error": "copilota non disponibile"}
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            return 400, {"error": "richiesta non valida"}
        return 200, {"job": self.start_job(*self.prompt_for(body))}

    def _job_status(self, match: re.Match[str], body: dict[str, Any], query: dict[str, str]) -> Response:
        job = self.jobs.get(match.group(1))
        if job is None:
            return 404, {"error": "non trovato"}
        after = int(query.get("after", "0")) if query.get("after", "0").isdigit() else 0
        return 200, job.snapshot(after)

    def _job_confirm(self, match: re.Match[str], body: dict[str, Any], query: dict[str, str]) -> Response:
        job = self.jobs.get(match.group(1))
        if job is None:
            return 404, {"error": "non trovato"}
        return 200, {"ok": job.confirm(bool(body.get("ok")))}


def make_handler(app: LocalApp, port_ref: list[int]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, status: int = 200) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in (f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}")

        def _authorized(self) -> bool:
            token = self.headers.get("X-AIOS-Token", "")
            return self._host_ok() and hmac.compare_digest(token.encode(), app.token.encode())

        def _body(self) -> dict[str, Any]:
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
                return data if isinstance(data, dict) else {}
            except ValueError:
                return {}

        def _dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            if not self._host_ok():
                return self._json({"error": "host non valido"}, 403)
            if method == "GET" and url.path in ("/", "/index.html"):
                return self._send(200, app.page.read_bytes(), "text/html; charset=utf-8")
            if method == "GET" and url.path == "/theme.css":  # colori del tema attivo: niente di privato
                from .themeapply import current_css

                return self._send(200, current_css().encode(), "text/css; charset=utf-8")
            if not self._authorized():
                return self._json({"error": "non autorizzato"}, 403)
            body = self._body() if method == "POST" else {}
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            for pattern, handler in app.routes[method]:
                m = pattern.fullmatch(url.path)
                if m:
                    try:
                        status, payload = handler(m, body, query)
                    except Exception as exc:  # mai un errore muto per la pagina
                        status, payload = 500, {"error": str(exc)}
                    return self._json(payload, status)
            self._json({"error": "non trovato"}, 404)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

    return Handler


def serve(app: LocalApp, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    port_ref = [port]
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port_ref))
    port_ref[0] = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port_ref[0]}/#t={app.token}"


def open_window(url: str, finished: threading.Event, title: str, app_id: str, size: tuple[int, int] = (980, 720)) -> None:
    """Finestra dedicata con WebKitGTK se disponibile, altrimenti il browser predefinito."""
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("WebKit", "6.0")
        from gi.repository import GLib, Gtk, WebKit

        gtk_app = Gtk.Application(application_id=app_id)

        def activate(application: Gtk.Application) -> None:
            window = Gtk.ApplicationWindow(application=application, title=title)
            window.set_default_size(*size)
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

        gtk_app.connect("activate", activate)
        gtk_app.run(None)
    except (ImportError, ValueError):
        import webbrowser

        webbrowser.open(url)
        print(f"{title} aperto nel browser: {url.split('#')[0]}  (Ctrl+C per chiudere)")
        try:
            finished.wait()
        except KeyboardInterrupt:
            pass
