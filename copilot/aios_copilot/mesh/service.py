"""Servizio telefono↔PC: collega e scollega tutto da solo.

Ogni pochi secondi:
- telefono abbinato arrivato vicino → avviso e pagina dei file accesa;
- telefono andato via → pagina dei file spenta (resta accesa solo durante un abbinamento);
- chiamata in arrivo → notifica con «Rispondi» e «Rifiuta».

Il copilota parla con il servizio da un socket locale leggibile solo dall'utente.

    aios-telefono servizio | stato | abbina
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .calls import Ofono
from .files import FileShare, PhoneServer, mesh_dir
from .phone import KdeConnect

TICK = 3.0


def control_path() -> Path:
    return mesh_dir() / "control.sock"


def notify_with_actions(title: str, body: str, actions: dict[str, str]) -> str:
    """Notifica con pulsanti; restituisce il pulsante premuto ("" se chiusa)."""
    cmd = ["notify-send", "--app-name=Copilota", "--urgency=critical", "--wait",
           *[f"--action={k}={v}" for k, v in actions.items()], title, body]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def simple_notify(title: str, body: str) -> None:
    try:
        subprocess.run(["notify-send", "--app-name=Copilota", "--icon=phone", title, body], timeout=10)
    except (OSError, subprocess.SubprocessError):
        print(f"[{title}] {body}", flush=True)


class MeshService:
    def __init__(self, kdeconnect: KdeConnect, ofono: Ofono, server: PhoneServer,
                 notify: Callable[[str, str], None] = simple_notify,
                 ask: Callable[[str, str, dict[str, str]], str] = notify_with_actions,
                 clock: Callable[[], float] = time.time, start_kwargs: dict[str, Any] | None = None,
                 bus: Any = None, copy: Callable[[str], bool] | None = None):
        self.kc, self.ofono, self.server = kdeconnect, ofono, server
        self.notify, self.ask, self.clock = notify, ask, clock
        self.start_kwargs = start_kwargs or {}
        self.near: dict[str, str] = {}  # id → nome
        self.ringing = ""
        self.bus, self.copy = bus, copy
        self.seen_codes: set[str] = set()
        self.sync_every, self.next_sync = 120.0, 0.0
        self.photos: Any = None  # PhotoSync: foto della fotocamera salvate da sole quando il telefono è vicino
        self.photos_every, self.next_photos = 1800.0, 0.0
        self._photos_busy = threading.Lock()

    def tick(self) -> None:
        try:
            phones = {p.id: p.name for p in self.kc.nearby()}
        except Exception:
            phones = dict(self.near)
        for pid, name in phones.items():
            if pid not in self.near:
                self.notify(f"📱 {name} collegato",
                            "Notifiche e chiamate arrivano sul PC; dal telefono puoi aprire i file del PC.")
        for pid, name in self.near.items():
            if pid not in phones:
                self.notify(f"📱 {name} si è allontanato", "Ho chiuso l'accesso ai file del PC.")
        self.near = phones
        pairing = bool(self.server.pairing.code) and self.clock() < self.server.pairing.expires
        if (phones or pairing) and not self.server.running:
            self.server.start(**self.start_kwargs)
        elif not phones and not pairing and self.server.running:
            self.server.stop()
        self._calls()
        self._codes()
        if self.photos is not None and phones and self.bus is not None and self.clock() >= self.next_photos:
            self.next_photos = self.clock() + self.photos_every
            threading.Thread(target=self.save_photos, daemon=True).start()
        if self.clock() >= self.next_sync:
            self.next_sync = self.clock() + self.sync_every
            threading.Thread(target=self.sync_now, daemon=True).start()

    def save_photos(self) -> None:
        from .photos import describe, mount_phone

        if not self._photos_busy.acquire(blocking=False):
            return
        try:
            for pid, name in list(self.near.items()):
                root = mount_phone(self.bus, pid)
                if root is None:
                    continue
                result = self.photos.run(pid, root, deadline=time.monotonic() + 900)
                if result["foto"] or result["video"]:
                    self.notify("📷 Foto salvate sul PC", describe(result, name))
        except Exception:
            pass
        finally:
            self._photos_busy.release()

    def sync_now(self) -> list[str]:
        """Sincronizzazione con gli altri dispositivi dell'utente (se c'è un'identità)."""
        from ..identity import Identity
        from .delegate import sync_peers

        identity = Identity.load()
        engine = self.server.sync() if getattr(self.server, "sync", None) else None
        if identity is None or engine is None:
            return []
        engine.scan()
        return sync_peers(identity, engine)

    def _codes(self) -> None:
        """Codice di verifica arrivato sul telefono → notifica sul PC con «Copia»."""
        if self.bus is None:
            return
        from .messages import otp_code

        for pid in self.near:
            try:
                notifications = self.bus.notifications(pid)
            except Exception:
                continue
            for n in notifications:
                code = otp_code(f"{n.title} {n.text}")
                key = f"{pid}/{n.id}/{code}"
                if not code or key in self.seen_codes:
                    continue
                self.seen_codes.add(key)

                def offer(code: str = code, source: str = n.app or n.title) -> None:
                    if self.ask(f"🔑 Codice {code}", f"Arrivato sul telefono da {source}.", {"copia": "Copia"}) == "copia" \
                            and self.copy:
                        self.copy(code)

                threading.Thread(target=offer, daemon=True).start()

    def _calls(self) -> None:
        try:
            call = self.ofono.incoming()
        except Exception:
            call = None
        if call is None:
            self.ringing = ""
            return
        if call.path == self.ringing:
            return
        self.ringing = call.path

        def ask() -> None:
            choice = self.ask(f"📞 Chiamata da {call.who}", "Rispondi dal PC o rifiuta. Puoi anche dirmi «rispondi».",
                              {"rispondi": "Rispondi", "rifiuta": "Rifiuta"})
            if choice == "rispondi":
                self.ofono.answer()
            elif choice == "rifiuta":
                self.ofono.hang_up()

        threading.Thread(target=ask, daemon=True).start()

    # --- comandi dal copilota ------------------------------------------------------------------
    def handle(self, command: dict[str, Any]) -> dict[str, Any]:
        action = command.get("azione")
        if action == "abbina":
            self.server.pairing.start()
            if not self.server.running:
                self.server.start(**self.start_kwargs)
            return {"url": self._url()}
        if action == "sincronizza":
            return {"righe": self.sync_now()}
        if action == "stato":
            return {"vicini": list(self.near.values()), "pagina": self.server.running,
                    "abbinati": [d.name for d in self.server.devices.items]}
        return {"errore": "comando sconosciuto"}

    def _url(self) -> str:
        from .files import lan_address

        port = self.server.httpd.server_address[1] if self.server.httpd else 0
        fp = getattr(self.server, "fingerprint", "")
        return f"https://{lan_address()}:{port}/#abbina={self.server.pairing.code}" + (f"&fp={fp}" if fp else "")

    def serve_control(self, path: Path | None = None) -> socket.socket:
        path = path or control_path()
        path.unlink(missing_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        os.chmod(path, 0o600)
        sock.listen(4)

        def loop() -> None:
            while True:
                try:
                    conn, _ = sock.accept()
                except OSError:
                    return
                with conn:
                    try:
                        request = json.loads(conn.recv(4096) or b"{}")
                        reply = self.handle(request if isinstance(request, dict) else {})
                    except Exception as exc:
                        reply = {"errore": str(exc)}
                    conn.sendall(json.dumps(reply).encode())

        threading.Thread(target=loop, daemon=True).start()
        return sock

    def run_forever(self) -> None:
        self.serve_control()
        while True:
            self.tick()
            time.sleep(TICK)


def send_command(command: dict[str, Any], path: Path | None = None) -> dict[str, Any] | None:
    """Dal copilota al servizio. None se il servizio non è attivo."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect(str(path or control_path()))
            s.sendall(json.dumps(command).encode())
            s.shutdown(socket.SHUT_WR)
            data = b""
            while chunk := s.recv(65536):
                data += chunk
        return json.loads(data)
    except (OSError, ValueError):
        return None


class _Lazy:
    """Crea il copilota per il telefono solo alla prima domanda (il servizio resta leggero)."""

    def __init__(self, factory: Callable[[], Any]):
        self._factory, self._obj = factory, None
        self._lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        with self._lock:
            if self._obj is None:
                self._obj = self._factory()
        return getattr(self._obj, name)


def build(search: Callable[[str], list[dict[str, Any]]] | None = None) -> MeshService:
    if search is None:
        def search(query: str) -> list[dict[str, Any]]:
            from ..fileindex import FileIndex

            return FileIndex().search(query, limit=20)
    from ..tools.base import Runner
    from ..tools.phone import copy_to_clipboard
    from .messages import PhoneBus

    runner = Runner()
    from .delegate import PHONE_ALLOWED, Assistant, Brain

    def phone_agent(confirm):
        from ..__main__ import make_agent

        return make_agent(confirm, allowed=PHONE_ALLOWED)

    engine: list = []

    def sync_engine():
        from ..identity import Identity
        from ..sync import engine_for

        if not engine:
            identity = Identity.load()
            made = engine_for(identity) if identity else None
            if made is None:
                return None
            engine.append(made)
        return engine[0]

    server = PhoneServer(FileShare(search=search), brain=Brain(), assistant=_Lazy(lambda: Assistant(phone_agent)),
                         sync=sync_engine)
    from .photos import PhotoSync

    service = MeshService(KdeConnect(runner), Ofono(runner), server, bus=PhoneBus(runner),
                          copy=lambda text: copy_to_clipboard(text, runner))
    service.photos = PhotoSync()
    return service


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv) or ["stato"]
    if args[0] == "servizio":
        build().run_forever()
    elif args[0] == "collega-pc" and len(args) > 1:  # sul telefono con AIOS: il QR mostrato dal PC
        from .delegate import pair_with_pc

        config = pair_with_pc(args[1], socket.gethostname())
        print(f"Collegato a {config['pc']}: quando è vicino, il copilota userà il suo modello AI.")
    elif args[0] in ("stato", "abbina"):
        reply = send_command({"azione": args[0]})
        if reply is None:
            print("Il servizio aios-telefono non è attivo (systemctl --user start aios-telefono).")
            return 1
        print(json.dumps(reply, ensure_ascii=False, indent=1))
    else:
        print("aios-telefono [servizio | stato | abbina | collega-pc URL]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
