"""Relay cifrato: sincronizzazione anche quando i dispositivi non sono nella stessa rete.

Il relay è una cassetta postale che non sa leggere. Conserva le operazioni di
sincronizzazione già cifrate end-to-end dai dispositivi (sync.py) e le consegna agli
altri dispositivi dello stesso utente.

Cosa vede il relay: l'impronta della cassetta (derivata dalla chiave pubblica
dell'utente), gli identificativi dei dispositivi che si collegano, quando e quanti
dati. Non vede: agenda, nomi, temi, chiavi delle voci, né la chiave di sincronizzazione.

Chi può entrare: solo un dispositivo con certificato firmato dall'utente e non
revocato; ogni richiesta è firmata e con l'orario (identity.py). Le revoche arrivano
firmate dall'utente: il relay le verifica e da lì in poi respinge il dispositivo.
Nessun registro degli indirizzi IP; limiti di spazio per cassetta.

    aios-relay --porta 8744 [--certificato cert.pem --chiave key.pem] [--dati relay.db]
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sqlite3
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .identity import mailbox_for, unb64, valid_revocations, verify_request

SCHEMA = """
CREATE TABLE IF NOT EXISTS mailboxes (id TEXT PRIMARY KEY, user_public TEXT NOT NULL, revocations TEXT DEFAULT '{}',
                                      bytes INTEGER DEFAULT 0, keys TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS ops (mailbox TEXT, h TEXT, ms INTEGER, c INTEGER, d TEXT, box TEXT, seq INTEGER,
                                PRIMARY KEY (mailbox, h));
CREATE INDEX IF NOT EXISTS ops_seq ON ops(mailbox, seq);
"""
MAX_BODY = 4_000_000
MAX_MAILBOX_BYTES = 64_000_000  # spazio per utente
MAX_OPS_REPLY = 2000
PATH = re.compile(r"^/v1/(?P<box>[0-9a-f]{32})/(?P<what>registra|ops|revoche|chiavi)$")


class RelayStore:
    def __init__(self, path: Path | str = ":memory:"):
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.executescript(SCHEMA)
        if "keys" not in [r[1] for r in self.db.execute("PRAGMA table_info(mailboxes)")]:
            self.db.execute("ALTER TABLE mailboxes ADD COLUMN keys TEXT DEFAULT '{}'")
        self.lock = threading.Lock()

    def owner(self, box: str) -> tuple[bytes, set[str], int] | None:
        row = self.db.execute("SELECT user_public, revocations FROM mailboxes WHERE id = ?", (box,)).fetchone()
        if row is None:
            return None
        revocations = json.loads(row[1] or "{}")
        return unb64(row[0]), set(revocations.get("revoked", [])), int(revocations.get("seq", -1))

    def register(self, box: str, user_public: bytes) -> bool:
        if mailbox_for(user_public) != box:
            return False  # la cassetta è legata alla chiave: nessuno può occupare quella di un altro
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO mailboxes (id, user_public) VALUES (?, ?)",
                            (box, base64.b64encode(user_public).decode()))
        return True

    def set_revocations(self, box: str, doc: dict) -> bool:
        owner = self.owner(box)
        if owner is None or not valid_revocations(doc, owner[0]) or int(doc.get("seq", -1)) <= owner[2]:
            return False
        with self.lock:
            self.db.execute("UPDATE mailboxes SET revocations = ? WHERE id = ?", (json.dumps(doc), box))
        return True

    def set_keys(self, box: str, doc: dict) -> bool:
        """Nuova chiave di sincronizzazione (dopo una revoca): i dati vecchi, cifrati con la chiave
        precedente, non servono più a nessuno e si cancellano."""
        from .ed25519 import verify
        from .identity import canonical

        owner = self.owner(box)
        if owner is None:
            return False
        try:
            body = {"v": doc["v"], "epoch": int(doc["epoch"]), "wrapped": doc["wrapped"]}
            current = json.loads(self.db.execute("SELECT keys FROM mailboxes WHERE id = ?", (box,)).fetchone()[0] or "{}")
            if body["epoch"] <= int(current.get("epoch", 0)) or not verify(owner[0], canonical(body), unb64(doc["signature"])):
                return False
        except (KeyError, TypeError, ValueError):
            return False
        with self.lock:
            self.db.execute("UPDATE mailboxes SET keys = ?, bytes = 0 WHERE id = ?", (json.dumps(doc), box))
            self.db.execute("DELETE FROM ops WHERE mailbox = ?", (box,))
        return True

    def put(self, box: str, ops: list[dict]) -> tuple[int, str]:
        """Si tiene solo la versione più recente di ogni voce (l'orologio è in chiaro, il contenuto no)."""
        stored = 0
        with self.lock:
            used = self.db.execute("SELECT bytes FROM mailboxes WHERE id = ?", (box,)).fetchone()[0]
            seq = self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM ops WHERE mailbox = ?", (box,)).fetchone()[0]
            for op in ops:
                try:
                    h, ms, c, d, payload = str(op["h"]), int(op["ms"]), int(op["c"]), str(op["d"]), str(op["box"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not re.fullmatch(r"[0-9a-f]{32}", h) or len(payload) > 1_000_000:
                    continue
                old = self.db.execute("SELECT ms, c, d, LENGTH(box) FROM ops WHERE mailbox = ? AND h = ?", (box, h)).fetchone()
                if old and tuple(old[:3]) >= (ms, c, d):
                    continue
                delta = len(payload) - (old[3] if old else 0)
                if used + delta > MAX_MAILBOX_BYTES:
                    return stored, "spazio esaurito"
                seq += 1
                used += delta
                self.db.execute("INSERT OR REPLACE INTO ops VALUES (?, ?, ?, ?, ?, ?, ?)", (box, h, ms, c, d, payload, seq))
                stored += 1
            self.db.execute("UPDATE mailboxes SET bytes = ? WHERE id = ?", (used, box))
        return stored, ""

    def since(self, box: str, after: int) -> tuple[list[dict], int]:
        rows = self.db.execute("SELECT h, ms, c, d, box, seq FROM ops WHERE mailbox = ? AND seq > ? ORDER BY seq LIMIT ?",
                               (box, after, MAX_OPS_REPLY)).fetchall()
        return [{"h": h, "ms": ms, "c": c, "d": d, "box": b} for h, ms, c, d, b, _ in rows], (rows[-1][5] if rows else after)


def make_handler(store: RelayStore) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "aios-relay"
        sys_version = ""

        def log_message(self, *args: Any) -> None:
            pass  # nessun registro degli indirizzi

        def _json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _handle(self, method: str) -> None:
            url = urlparse(self.path)
            m = PATH.match(url.path)
            if not m:
                return self._json({"error": "non trovato"}, 404)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json({"error": "richiesta troppo grande"}, 413)
            raw = self.rfile.read(length) if method == "POST" else b""
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return self._json({"error": "richiesta non valida"}, 400)
            box, what = m.group("box"), m.group("what")
            header = self.headers.get("Authorization", "")

            if method == "POST" and what == "registra":
                try:
                    user_public = unb64(str(body.get("utente", "")))
                except ValueError:
                    return self._json({"error": "chiave non valida"}, 400)
                # anche la registrazione deve venire da un dispositivo dell'utente
                if verify_request(header, method, self.path, raw, user_public) is None or not store.register(box, user_public):
                    return self._json({"error": "dispositivo non riconosciuto"}, 403)
                if isinstance(body.get("revoche"), dict):
                    store.set_revocations(box, body["revoche"])
                return self._json({"ok": True})

            owner = store.owner(box)
            if owner is None:
                return self._json({"error": "cassetta sconosciuta: registrala"}, 404)
            if verify_request(header, method, self.path, raw, owner[0], owner[1]) is None:
                return self._json({"error": "dispositivo non riconosciuto"}, 403)
            if what == "revoche" and method == "POST":
                return self._json({"ok": store.set_revocations(box, body)})
            if what == "chiavi" and method == "POST":
                return self._json({"ok": store.set_keys(box, body)})
            if what == "ops" and method == "GET":
                after = parse_qs(url.query).get("dopo", ["0"])[0]
                ops, seq = store.since(box, int(after) if after.isdigit() else 0)
                row = store.db.execute("SELECT revocations, keys FROM mailboxes WHERE id = ?", (box,)).fetchone()
                return self._json({"ops": ops, "seq": seq, "revoche": json.loads(row[0] or "{}"),
                                   "chiavi": json.loads(row[1] or "{}")})
            if what == "ops" and method == "POST":
                ops = body.get("ops")
                stored, error = store.put(box, ops if isinstance(ops, list) else [])
                return self._json({"salvate": stored, **({"error": error} if error else {})}, 507 if error else 200)
            return self._json({"error": "non trovato"}, 404)

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

    return Handler


def serve(store: RelayStore, host: str = "0.0.0.0", port: int = 8744, cert: Path | None = None,
          key: Path | None = None) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(store))
    if cert and key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# --- lato dispositivo ----------------------------------------------------------------------------------


def relay_requester(identity: Any, relay: dict[str, str], request: Any = None):
    """Adatta le chiamate di sync.SyncEngine.sync_with ai percorsi del relay, con richieste firmate.

    relay = {"url": "https://…", "fingerprint": "…"(facoltativa: certificato fissato; senza, verifica CA)}.
    """
    from .mesh.delegate import accept_updates, pinned_request

    base = relay["url"].rstrip("/")
    prefix = f"/v1/{identity.mailbox}"

    def send(method: str, path: str, payload: Any) -> Any:
        body = json.dumps(payload).encode() if payload is not None else b""
        auth = identity.sign_request(method, path, body)
        if request is not None:
            return request(base, method, path, payload, auth, relay.get("fingerprint", ""))
        if relay.get("fingerprint"):
            return pinned_request(base, method, path, payload, auth, relay["fingerprint"], 60)
        return _ca_request(base, method, path, body if payload is not None else None, auth)

    registered = [False]

    def call(method: str, path: str, payload: Any) -> Any:
        if not registered[0]:
            send("POST", f"{prefix}/registra", {"utente": identity.data["utente"], "revoche": identity.data.get("revoche", {})})
            registered[0] = True
        if identity.master() is not None:  # il dispositivo principale comunica revoche e nuove chiavi
            if identity.data.get("revoche", {}).get("seq", 0):
                send("POST", f"{prefix}/revoche", identity.data["revoche"])
            if identity.data.get("chiavi", {}).get("epoch", 0):
                send("POST", f"{prefix}/chiavi", identity.data["chiavi"])
        if path.startswith("/api/sync"):
            path = f"{prefix}/ops" + path[len("/api/sync"):]
        return accept_updates(identity, send(method, path, payload))

    return call


def _ca_request(base: str, method: str, path: str, body: bytes | None, auth: str) -> Any:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base + path, data=body, method=method,
                                 headers={"Content-Type": "application/json", "Authorization": auth})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # certificato verificato dalle autorità (CA)
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read()).get("error", "")
        except ValueError:
            message = ""
        raise ConnectionError(message or f"errore {exc.code}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aios-relay", description="Relay cifrato per la sincronizzazione di SoIA")
    parser.add_argument("--porta", type=int, default=8744)
    parser.add_argument("--indirizzo", default="0.0.0.0")
    parser.add_argument("--dati", default="relay.db")
    parser.add_argument("--certificato")
    parser.add_argument("--chiave")
    args = parser.parse_args(argv)
    if not (args.certificato and args.chiave):
        print("Attenzione: senza --certificato e --chiave il relay va messo dietro un proxy HTTPS.", file=sys.stderr)
    serve(RelayStore(args.dati), args.indirizzo, args.porta,
          Path(args.certificato) if args.certificato else None, Path(args.chiave) if args.chiave else None)
    print(f"Relay SoIA in ascolto sulla porta {args.porta}.")
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
