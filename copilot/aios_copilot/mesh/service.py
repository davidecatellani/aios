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
    cmd = ["notify-send", "--app-name=Nova", "--urgency=critical", "--wait",
           *[f"--action={k}={v}" for k, v in actions.items()], title, body]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def simple_notify(title: str, body: str) -> None:
    try:
        subprocess.run(["notify-send", "--app-name=Nova", "--icon=phone", title, body], timeout=10)
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
        self.energy: Any = None  # EnergyBrain: Nova decide ogni quanto fare cosa, in base alla batteria
        self.last: dict[str, float] = {}
        self.photos: Any = None  # PhotoSync: foto della fotocamera salvate da sole quando il telefono è vicino
        self._photos_busy = threading.Lock()
        self.bluetooth: Any = None  # dispositivi Bluetooth dell'utente condivisi tra i suoi dispositivi
        self.nearby: Any = None  # Nearby: collegamento diretto con il telefono anche senza Wi-Fi (nearby.py)
        self._nearby_busy = threading.Lock()
        self._nearby_status = ""

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
        linked = self.nearby is not None and bool(self.nearby.link)  # collegamento diretto senza Wi-Fi
        if (phones or pairing or linked) and not self.server.running:
            self.server.start(**self.start_kwargs)
        elif not phones and not pairing and not linked and self.server.running:
            self.server.stop()
        self._calls()
        self._codes()
        intervals = self._intervals()
        if self.photos is not None and phones and self.bus is not None and self._due("foto", intervals):
            threading.Thread(target=self.save_photos, daemon=True).start()
        if self.nearby is not None and self._due("vicini", intervals):
            threading.Thread(target=self._nearby_round, args=(bool(phones), intervals), daemon=True).start()
        if self.bluetooth is not None and self._due("bluetooth", intervals):
            threading.Thread(target=self._bluetooth_round, daemon=True).start()
        if self._due("sincronizzazione", intervals):
            threading.Thread(target=self.sync_now, daemon=True).start()

    def _intervals(self) -> dict[str, int | None]:
        """Ogni quanto fare cosa: lo decide Nova (energy.py); senza, i valori di quando si è in carica."""
        from ..energy import ACTIVITIES

        if self.energy is None:
            return {k: v[0] for k, v in ACTIVITIES.items()}
        try:
            self.energy.observe()
            return self.energy.decide().intervals
        except Exception:
            return {k: v[0] for k, v in ACTIVITIES.items()}

    def _due(self, activity: str, intervals: dict[str, int | None]) -> bool:
        interval = intervals.get(activity)
        now = self.clock()
        if interval is None or now - self.last.get(activity, -1e18) < interval:
            return False
        self.last[activity] = now
        return True

    def _nearby_round(self, same_network: bool, intervals: dict[str, int | None]) -> None:
        """Senza una rete in comune: Nova cerca il telefono via Bluetooth e sceglie come collegarsi.
        Collegamento veloce (Wi-Fi diretto) quando c'è lavoro pesante e la batteria lo permette."""
        if not self._nearby_busy.acquire(blocking=False):
            return
        try:
            heavy = intervals.get("foto") is not None
            low = intervals.get("bluetooth") is None and self.energy is not None
            status = self.nearby.round(same_network, "pesante" if heavy else "leggero", low,
                                       confirm=lambda why: self.ask("🌐 Usare internet del telefono?", why,
                                                                    {"si": "Sì", "no": "No"}) == "si")
            if status.startswith("collegato") and status != self._nearby_status:
                self.notify("📶 Telefono collegato", self.nearby.status())
            self._nearby_status = status
        except Exception:
            pass
        finally:
            self._nearby_busy.release()

    def _bluetooth_round(self) -> None:
        from .bluetooth import share_round

        try:
            share_round(self.bluetooth, self.notify, self.ask)
        except Exception:
            pass

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
            reply = {"vicini": list(self.near.values()), "pagina": self.server.running,
                     "abbinati": [d.name for d in self.server.devices.items]}
            if self.nearby is not None:
                reply["collegamento"] = self.nearby.status()
            return reply
        if action == "vicino":  # «collegati al telefono», «usa internet del telefono», «scollega»
            if self.nearby is None:
                return {"errore": "collegamento senza Wi-Fi non disponibile su questo PC"}
            kind = str(command.get("tipo", "auto"))
            if kind == "chiudi":
                return {"testo": self.nearby.release()}
            if kind not in ("auto", "wifi", "bluetooth", "internet"):
                return {"errore": "tipo sconosciuto"}
            self.nearby.request(kind)
            self.last.pop("vicini", None)  # cerca subito
            self._nearby_round(bool(self.near), {"foto": 1, "bluetooth": 1})
            return {"testo": self.nearby.status()}
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

    from .remote_input import InputInjector

    server = PhoneServer(FileShare(search=search), brain=Brain(), sync=sync_engine)
    server.assistant = _Lazy(lambda: Assistant(phone_agent, attach=server.attach))
    server.input = InputInjector(runner)
    from .photos import PhotoSync

    service = MeshService(KdeConnect(runner), Ofono(runner), server, bus=PhoneBus(runner),
                          copy=lambda text: copy_to_clipboard(text, runner))
    service.photos = PhotoSync()
    from ..energy import EnergyBrain

    service.energy = EnergyBrain()
    from .bluetooth import Bluetooth

    service.bluetooth = Bluetooth(runner)
    from .nearby import BleAdvertiser, BleScanner, Links, Nearby

    scanner, links = BleScanner(runner), Links(runner)
    if scanner.available() and links.available():
        service.nearby = Nearby(scanner, BleAdvertiser(), links, lambda: nearby_secrets(server.devices))
    return service


def nearby_secrets(devices: Any) -> list:
    """I segreti per riconoscere i propri dispositivi: telefoni abbinati (dal più recente) e identità AIOS."""
    from ..identity import Identity
    from .nearby import secret_from_device, secret_from_sync_key

    found = [secret_from_device(d.key_hash, d.name, d.bt)
             for d in sorted(devices.items, key=lambda d: d.last_seen or d.added, reverse=True)]
    try:
        identity = Identity.load()
        key = identity.sync_key() if identity else None
        if key:
            found.append(secret_from_sync_key(key))
    except Exception:
        pass
    return found


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv) or ["stato"]
    if args[0] == "servizio":
        build().run_forever()
    elif args[0] == "collega-pc" and len(args) > 1:  # sul telefono con AIOS: il QR mostrato dal PC
        from .delegate import pair_with_pc

        config = pair_with_pc(args[1], socket.gethostname())
        print(f"Collegato a {config['pc']}: quando è vicino, Nova userà il suo modello AI.")
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
