"""L'accesso ad SoIA: la schermata per entrare, in HTML come il resto del sistema (niente GDM).

Gira come utente «greeter» sotto greetd, dentro cage (un compositore che mostra una sola app a
schermo intero): una finestra WebKit con accesso.html servita da 127.0.0.1. La pagina chiede la
password; qui la si passa a greetd con il suo protocollo (JSON con la lunghezza davanti, sul socket
$GREETD_SOCK) e, se è giusta, greetd avvia la sessione di SoIA (aios-sessione) e questa app esce.

Se qualcosa non va (manca WebKit, la finestra non parte), aios-accesso-avvio ripiega sull'accesso
testuale di greetd (agreety), e se greetd stesso fallisce systemd riavvia GDM: non si resta mai fuori.
"""

from __future__ import annotations

import json
import os
import pwd
import socket
import struct
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..localapp import LocalApp, serve

PAGE = Path(__file__).with_name("accesso.html")
SESSION = ["/usr/bin/aios-sessione"]
SESSION_ENV = ["XDG_SESSION_TYPE=wayland", "XDG_SESSION_DESKTOP=aios", "XDG_CURRENT_DESKTOP=AIOS"]


class Greetd:
    """Il protocollo di greetd: messaggi JSON preceduti dalla lunghezza (4 byte, ordine nativo)."""

    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("GREETD_SOCK", "")

    def _call(self, sock: socket.socket, msg: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(msg).encode()
        sock.sendall(struct.pack("=I", len(data)) + data)
        head = self._read(sock, 4)
        return json.loads(self._read(sock, struct.unpack("=I", head)[0]))

    @staticmethod
    def _read(sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("greetd ha chiuso la connessione")
            buf += chunk
        return buf

    def login(self, user: str, password: str, cmd: list[str] = SESSION, env: list[str] = SESSION_ENV) -> tuple[bool, str]:
        """Prova l'accesso; se riesce, la sessione parte appena il greeter esce."""
        if not self.path:
            return False, "greetd non è in esecuzione"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(60)
            sock.connect(self.path)
            reply = self._call(sock, {"type": "create_session", "username": user})
            answered = False
            while reply.get("type") == "auth_message":
                kind = reply.get("auth_message_type")
                if kind in ("secret", "visible") and not answered:
                    reply = self._call(sock, {"type": "post_auth_message_response", "response": password})
                    answered = True
                elif kind in ("info", "error"):
                    reply = self._call(sock, {"type": "post_auth_message_response", "response": None})
                else:  # una seconda domanda (es. password scaduta): non gestita qui
                    self._call(sock, {"type": "cancel_session"})
                    return False, "Serve un passaggio in più per questo account: entra dall'accesso testuale (Ctrl+Alt+F2)."
            if reply.get("type") == "success":
                reply = self._call(sock, {"type": "start_session", "cmd": cmd, "env": env})
                if reply.get("type") == "success":
                    return True, ""
            try:  # greetd vuole che la sessione fallita sia annullata prima di riprovare
                self._call(sock, {"type": "cancel_session"})
            except (OSError, ValueError, ConnectionError):
                pass
            if reply.get("error_type") == "auth_error":
                return False, "Password sbagliata."
            return False, reply.get("description") or "Non riesco ad avviare la sessione."


def people(passwd: Callable[[], list[Any]] = pwd.getpwall) -> list[dict[str, str]]:
    """Le persone che possono entrare: utenti normali con una shell vera."""
    found = []
    for p in passwd():
        if 1000 <= p.pw_uid < 60000 and not p.pw_shell.endswith(("nologin", "false")):
            full = (p.pw_gecos or "").split(",")[0].strip()
            found.append({"utente": p.pw_name, "nome": full or p.pw_name.title()})
    return sorted(found, key=lambda x: x["nome"].lower())


class AccessApp(LocalApp):
    page = PAGE

    def __init__(self, greetd: Greetd | None = None, users: Callable[[], list[dict[str, str]]] = people,
                 run: Callable[[list[str]], int] | None = None):
        super().__init__(None)
        self.greetd = greetd or Greetd()
        self.run = run or (lambda c: subprocess.run(c, capture_output=True).returncode)
        self.logged_in = threading.Event()
        self.users = users
        self.route("GET", r"/api/persone", lambda m, b, q: (200, {"persone": users(), "ora": datetime.now().isoformat()}))
        self.route("POST", r"/api/entra", self._enter)
        self.route("POST", r"/api/energia", self._power)

    def _enter(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        user = str(body.get("utente", ""))
        if user not in {p["utente"] for p in self.users()}:
            return 200, {"ok": False, "messaggio": "Utente sconosciuto."}
        try:
            ok, msg = self.greetd.login(user, str(body.get("password", "")))
        except (OSError, ValueError, ConnectionError) as exc:
            ok, msg = False, f"Non riesco a parlare con greetd: {exc}"
        if ok:
            self.logged_in.set()
        return 200, {"ok": ok, "messaggio": msg}

    def _power(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        action = {"spegni": "poweroff", "riavvia": "reboot", "sospendi": "suspend"}.get(str(body.get("azione")))
        if action is None:
            return 400, {"error": "azione sconosciuta"}
        return 200, {"ok": self.run(["systemctl", action]) == 0}


def run_window(app: AccessApp, url: str) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("WebKit", "6.0")
    from gi.repository import GLib, Gtk, WebKit

    gtk_app = Gtk.Application(application_id="org.aios.Accesso")

    def activate(application: Any) -> None:
        win = Gtk.ApplicationWindow(application=application, title="SoIA")
        win.set_decorated(False)
        view = WebKit.WebView()
        view.load_uri(url)
        win.set_child(view)
        win.fullscreen()
        win.present()

        def check() -> bool:
            if app.logged_in.is_set():
                GLib.timeout_add(600, application.quit)  # il tempo per l'animazione di entrata
                return False
            return True

        GLib.timeout_add(200, check)

    gtk_app.connect("activate", activate)
    status = gtk_app.run([sys.argv[0]])
    return 0 if app.logged_in.is_set() or status == 0 else 1


def main(argv: list[str] | None = None) -> int:
    app = AccessApp()
    server, url = serve(app)
    try:
        return run_window(app, url)
    except Exception as exc:  # niente finestra: aios-accesso-avvio passa all'accesso testuale
        print(f"aios-accesso: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
