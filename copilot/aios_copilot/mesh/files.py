"""I file del PC dal telefono: una pagina sicura in rete locale.

Sicurezza:
- HTTPS con un certificato del PC (creato una volta sola);
- abbinamento con un codice QR mostrato sullo schermo del PC: chi lo inquadra è
  davanti al PC. Il codice vale una volta sola e per 5 minuti; il telefono riceve una
  chiave personale (sul PC se ne conserva solo l'impronta) e la si può revocare;
- solo lettura, solo nella cartella personale, mai file nascosti né quelli esclusi
  dalla privacy (chiavi, password, profili dei browser…), mai fuori tramite link;
- il servizio è acceso solo mentre un telefono abbinato è vicino (service.py).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import socket
import ssl
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse

from ..privacy import is_excluded, private_dir

PORT = 8743
PAIR_SECONDS = 300
MAX_PAIR_TRIES = 10
TICKET_SECONDS = 120
PAGE = Path(__file__).with_name("phone.html")


def mesh_dir() -> Path:
    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "mesh")


@dataclass
class Device:
    name: str
    key_hash: str
    added: float
    last_seen: float = 0.0


class Devices:
    """Telefoni abbinati alla pagina dei file (si conserva solo l'impronta della chiave)."""

    def __init__(self, path: Path | None = None):
        self.path = path or mesh_dir() / "devices.json"
        self.lock = threading.Lock()
        try:
            self.items = [Device(**d) for d in json.loads(self.path.read_text())]
        except (OSError, ValueError, TypeError):
            self.items = []

    def save(self) -> None:
        self.path.write_text(json.dumps([asdict(d) for d in self.items], indent=1))
        os.chmod(self.path, 0o600)

    def add(self, name: str) -> str:
        key = secrets.token_urlsafe(32)
        with self.lock:
            self.items.append(Device(name[:60] or "telefono", _hash(key), time.time()))
            self.save()
        return key

    def check(self, key: str) -> Device | None:
        h = _hash(key)
        for d in self.items:
            if hmac.compare_digest(d.key_hash, h):
                d.last_seen = time.time()
                return d
        return None

    def remove(self, name: str) -> int:
        with self.lock:
            before = len(self.items)
            self.items = [d for d in self.items if name.lower() not in d.name.lower()]
            self.save()
            return before - len(self.items)


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def certificate(folder: Path | None = None) -> tuple[Path, Path, str]:
    """Certificato del PC per HTTPS (creato la prima volta). → (cert, chiave, impronta SHA-256)."""
    folder = folder or mesh_dir()
    cert, key = folder / "pc.crt", folder / "pc.key"
    if not cert.exists() or not key.exists():
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-days", "3650", "-subj", f"/CN=AIOS {socket.gethostname()[:40]}",
                        "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
        os.chmod(key, 0o600)
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return cert, key, hashlib.sha256(der).hexdigest()


def lan_address() -> str:
    """L'indirizzo del PC nella rete locale (nessun pacchetto viene inviato)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Pairing:
    """Un codice monouso per abbinare un telefono, valido pochi minuti."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.code = ""
        self.expires = 0.0
        self.tries = 0

    def start(self) -> str:
        self.code, self.expires, self.tries = secrets.token_urlsafe(18), self.clock() + PAIR_SECONDS, 0
        return self.code

    def use(self, code: str) -> bool:
        if not self.code or self.clock() > self.expires or self.tries >= MAX_PAIR_TRIES:
            return False
        self.tries += 1
        if hmac.compare_digest(code.encode(), self.code.encode()):
            self.code = ""  # vale una volta sola
            return True
        return False


class FileShare:
    """Cosa può vedere il telefono: la cartella personale, in sola lettura, senza le parti private."""

    def __init__(self, root: Path | None = None, search: Callable[[str], list[dict[str, Any]]] | None = None):
        self.root = (root or Path.home()).resolve()
        self.search_fn = search

    def resolve(self, rel: str) -> Path | None:
        rel = rel.strip("/")
        if any(part.startswith(".") for part in Path(rel).parts):
            return None  # niente file o cartelle nascosti (configurazioni, chiavi)
        path = (self.root / rel).resolve()  # risolve anche i collegamenti simbolici
        if path != self.root and self.root not in path.parents:
            return None
        if path != self.root and is_excluded(path):
            return None
        return path if path.exists() else None

    def listing(self, rel: str) -> dict[str, Any] | None:
        folder = self.resolve(rel)
        if folder is None or not folder.is_dir():
            return None
        entries = []
        for child in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith(".") or is_excluded(child):
                continue
            try:
                real = child.resolve()
                if real != self.root and self.root not in real.parents:
                    continue
                st = child.stat()
            except OSError:
                continue
            entries.append({"name": child.name, "dir": child.is_dir(), "size": st.st_size, "modified": int(st.st_mtime),
                            "path": str(child.relative_to(self.root))})
        return {"path": str(folder.relative_to(self.root)) if folder != self.root else "", "entries": entries[:2000]}

    def search(self, query: str) -> list[dict[str, Any]]:
        if not self.search_fn or not query.strip():
            return []
        results = []
        for r in self.search_fn(query):
            path = Path(str(r.get("path", "")))
            try:
                rel = str(path.resolve().relative_to(self.root))
            except ValueError:
                continue
            if self.resolve(rel) is not None:
                results.append({"name": path.name, "path": rel, "snippet": str(r.get("snippet", ""))[:300]})
        return results


class PhoneServer:
    def __init__(self, share: FileShare, devices: Devices | None = None, pairing: Pairing | None = None):
        self.share = share
        self.devices = devices or Devices()
        self.pairing = pairing or Pairing()
        self.httpd: ThreadingHTTPServer | None = None
        self.tickets: dict[str, tuple[Path, float]] = {}

    def ticket(self, path: Path) -> str:
        """Link per un solo file, valido 2 minuti (si può riusare per riprendere un video)."""
        now = time.time()
        self.tickets = {k: v for k, v in self.tickets.items() if v[1] > now}
        t = secrets.token_urlsafe(24)
        self.tickets[t] = (path, now + TICKET_SECONDS)
        return t

    def redeem(self, ticket: str) -> Path | None:
        entry = self.tickets.get(ticket)
        return entry[0] if entry and entry[1] > time.time() else None

    @property
    def running(self) -> bool:
        return self.httpd is not None

    def start(self, host: str = "0.0.0.0", port: int = PORT, cert_dir: Path | None = None) -> int:
        if self.httpd is not None:
            return self.httpd.server_address[1]
        cert, key, _ = certificate(cert_dir)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        httpd = ThreadingHTTPServer((host, port), make_handler(self))
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        self.httpd = httpd
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd.server_address[1]

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


def make_handler(server: PhoneServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _headers(self, status: int, content_type: str, length: int, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Strict-Transport-Security", "max-age=31536000")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def _json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _device(self) -> Device | None:
            auth = self.headers.get("Authorization", "")
            return server.devices.check(auth[7:]) if auth.startswith("Bearer ") else None

        def do_GET(self) -> None:
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path in ("/", "/index.html"):
                body = PAGE.read_bytes()
                self._headers(200, "text/html; charset=utf-8", len(body))
                return self.wfile.write(body)
            if url.path.startswith("/scarica/"):
                return self._download(url.path[9:], bool(query.get("vedi")))
            if self._device() is None:
                return self._json({"error": "telefono non abbinato"}, 403)
            if url.path == "/api/cartella":
                listing = server.share.listing(query.get("p", ""))
                return self._json(listing) if listing is not None else self._json({"error": "non trovato"}, 404)
            if url.path == "/api/cerca":
                return self._json({"results": server.share.search(query.get("q", "")[:200])})
            if url.path == "/api/link":  # link monouso per aprire o scaricare (anche video grandi)
                path = server.share.resolve(query.get("p", ""))
                if path is None or not path.is_file():
                    return self._json({"error": "non trovato"}, 404)
                return self._json({"url": f"/scarica/{server.ticket(path)}"})
            self._json({"error": "non trovato"}, 404)

        def _download(self, ticket: str, view: bool) -> None:
            path = server.redeem(ticket)
            if path is None or not path.is_file():
                return self._json({"error": "link scaduto"}, 403)
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            viewable = kind.startswith(("image/", "audio/", "video/")) or kind in ("application/pdf", "text/plain")
            mode = "inline" if view and viewable else "attachment"
            self._headers(200, kind, path.stat().st_size, {"Content-Disposition": f"{mode}; filename*=UTF-8''{quote(path.name)}"})
            with path.open("rb") as f:
                while chunk := f.read(1 << 16):
                    self.wfile.write(chunk)

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/abbina":
                return self._json({"error": "non trovato"}, 404)
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)) or b"{}")
            except ValueError:
                body = {}
            if not isinstance(body, dict) or not server.pairing.use(str(body.get("code", ""))):
                return self._json({"error": "codice non valido o scaduto: inquadra di nuovo il QR sul PC"}, 403)
            name = str(body.get("name") or "telefono")
            return self._json({"key": server.devices.add(name), "pc": socket.gethostname()})

    return Handler
