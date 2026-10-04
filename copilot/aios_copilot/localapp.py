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
    "Per le domande libere mi serve il mio modello AI, che non è ancora pronto: lo scarico appena c'è "
    "internet. Intanto posso già fare molte cose: prova «alza il volume», «che ore sono» o «quanta memoria ho?»."
)
MAX_BODY = 256 * 1024
BIG_BODY = 48 * 1024 * 1024  # solo per le rotte in LocalApp.big_body (es. un'immagine modificata)

Response = tuple[int, Any]  # (stato HTTP, contenuto JSON, o Raw)
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; media-src 'self' blob:; frame-src 'self'; connect-src 'self'; frame-ancestors 'self'")


@dataclass
class Raw:
    """Una risposta che non è JSON: un file dell'utente (con Range, per audio e video) o una pagina."""

    body: bytes | Path
    content_type: str
    csp: str = CSP
    filename: str = ""


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
    # File statici della pagina (/static/nome): script e stili, niente di privato.
    static_dir: Path | None = None
    # Rotte che si possono aprire da <img>, <video>, <iframe> (che non mandano l'header):
    # la chiave arriva nel parametro «t».
    token_in_query = ("/file/", "/doc/", "/api/miniatura")
    big_body = ("/api/file/salva-immagine",)

    def __init__(self, make_agent: Callable[[Callable[..., bool]], Agent] | None = None):
        self.token = secrets.token_urlsafe(24)
        self.jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._local = threading.local()  # la richiesta di questo thread (conferme): ce ne possono essere due
        self._agent_lock = threading.Lock()
        self.finished = threading.Event()
        self.on_answer: Callable[[str, str], Any] | None = None  # la shell annota la conversazione nel diario
        # Un solo agente per tutta la sessione: ricorda il contesto.
        self.agent = make_agent(lambda tool, args, **kw: self._local.job.ask_confirmation(tool, args, **kw)) \
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
            elif kind == "token":
                job.add(kind="token", text=data["text"])

        from .tools.base import set_attach_sink

        def on_cards(kind: str, items: list[dict[str, Any]], title: str) -> None:
            job.add(kind="schede", tipo=kind, titolo=title, elementi=items)

        self._local.job = job
        set_attach_sink(on_cards)
        try:
            # Il modello sta lavorando a un'altra richiesta: i comandi partono subito (corsia veloce), il resto
            # aspetta il suo turno e la risposta arriva sotto la sua domanda.
            if not self._agent_lock.acquire(blocking=False):
                quick = getattr(self.agent, "quick", None)
                job.answer = quick(text, on_event) if quick is not None else None
                if job.answer is None:
                    job.add(kind="coda")
                    self._agent_lock.acquire()
            else:
                job.answer = None
            if job.answer is None:
                try:
                    job.answer = self._answer(job, text, context, on_event, on_cards)
                finally:
                    self._agent_lock.release()
        except LLMError as exc:  # messaggio già in parole semplici (llm.py) o quello generico
            job.answer = str(exc) if str(exc).startswith("Il mio modello") else NO_MODEL
        except Exception as exc:  # la pagina deve sempre ricevere una risposta
            job.answer = f"Qualcosa è andato storto: {exc}"
        finally:
            set_attach_sink(None)
            self._local.job = None
        if self.on_answer is not None and job.answer:
            try:
                self.on_answer(text, job.answer)
            except Exception:  # il diario non deve mai togliere la risposta
                pass

    def _answer(self, job: Job, text: str, context: str | None, on_event: Callable[..., None],
                on_cards: Callable[..., None]) -> str:
        answer = self.agent.ask(text, on_event, context)
        from .schede import media_cards

        cards = media_cards(text, answer or "")
        if cards and not any(e.get("kind") == "schede" for e in job.events):
            on_cards("media", cards, "Ti propongo")
        return answer

    def prompt_for(self, body: dict[str, Any]) -> tuple[str, str | None]:
        """Frase da passare al copilota e contesto per il modello (le app lo specializzano)."""
        return body["text"].strip(), None

    def _ask(self, match: re.Match[str], body: dict[str, Any], query: dict[str, str]) -> Response:
        text = body.get("text")
        if self.agent is None:
            return 404, {"error": "Nova non è disponibile"}
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

        def _send(self, status: int, body: bytes, content_type: str, csp: str = CSP,
                  extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", csp)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _raw(self, status: int, raw: Raw) -> None:
            if isinstance(raw.body, bytes):
                return self._send(status, raw.body, raw.content_type, raw.csp)
            size = raw.body.stat().st_size
            start, end = 0, size - 1
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:  # gli ultimi N byte
                    start = max(0, size - int(m.group(2)))
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206
            self.send_response(status)
            self.send_header("Content-Type", raw.content_type)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "private, max-age=60")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", raw.csp)
            self.end_headers()
            with raw.body.open("rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return  # il lettore video ha chiesto un altro pezzo
                    left -= len(chunk)

        def _json(self, payload: Any, status: int = 200) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in (f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}")

        def _authorized(self, path: str = "", query: dict[str, str] | None = None) -> bool:
            token = self.headers.get("X-AIOS-Token", "")
            if not token and path.startswith(app.token_in_query):
                token = (query or {}).get("t", "")
            return self._host_ok() and hmac.compare_digest(token.encode(), app.token.encode())

        def _body(self) -> dict[str, Any]:
            limit = BIG_BODY if self.path.split("?", 1)[0] in app.big_body else MAX_BODY
            length = min(int(self.headers.get("Content-Length") or 0), limit)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
                return data if isinstance(data, dict) else {}
            except ValueError:
                return {}

        def _brand(self, name: str) -> None:
            from .branding import brand_file

            found = brand_file(name)
            if found is None:
                return self._json({"error": "non trovato"}, 404)
            data, kind = found
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            if not self._host_ok():
                return self._json({"error": "host non valido"}, 403)
            if method == "GET" and url.path in ("/", "/index.html"):
                return self._send(200, app.page.read_bytes(), "text/html; charset=utf-8")
            if method == "GET" and url.path.startswith("/brand/"):  # loghi di AIOS: pubblici
                return self._brand(url.path[7:])
            if method == "GET" and url.path == "/theme.css":  # colori del tema attivo: niente di privato
                from .themeapply import current_css

                return self._send(200, current_css().encode(), "text/css; charset=utf-8")
            if method == "GET" and url.path.startswith("/static/") and app.static_dir is not None:
                return self._static(url.path[8:])
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if not self._authorized(url.path, query):
                return self._json({"error": "non autorizzato"}, 403)
            body = self._body() if method == "POST" else {}
            for pattern, handler in app.routes[method]:
                m = pattern.fullmatch(url.path)
                if m:
                    try:
                        status, payload = handler(m, body, query)
                    except Exception as exc:  # mai un errore muto per la pagina
                        status, payload = 500, {"error": str(exc)}
                    if isinstance(payload, Raw):
                        return self._raw(status, payload)
                    return self._json(payload, status)
            self._json({"error": "non trovato"}, 404)

        def _static(self, name: str) -> None:
            kinds = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                     ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8"}
            path = (app.static_dir / name) if re.fullmatch(r"[\w-]+\.(js|css|svg|html)", name) else None
            if path is None or not path.is_file():
                return self._json({"error": "non trovato"}, 404)
            self._send(200, path.read_bytes(), kinds[path.suffix])

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
