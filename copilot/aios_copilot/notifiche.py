"""Il centro notifiche: la cronologia di tutte le notifiche (anche dopo un riavvio) e «Non disturbare».

- Le notifiche le mostra mako (con i colori di SoIA); SoIA le ascolta sul bus di sessione (monitor D-Bus,
  sola lettura) e le tiene in ~/.local/share/aios/notifiche.json: le ultime 300, leggibili solo dall'utente.
- «Non disturbare»: a mano (anche per un'ora o fino a domattina), con orari fissi, e da solo mentre si gioca.
  Le notifiche arrivano lo stesso in cronologia, solo non compaiono; quelle urgenti (critical) passano.
  Si applica con la modalità «non-disturbare» di mako (makoctl mode).
- Nova le riassume: «cosa mi sono perso?».
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

MAX_ITEMS = 300
MODE = "non-disturbare"
DEFAULTS = {"attivo": False, "fino_a": "", "nei_giochi": True, "orari": False, "dalle": "22:00", "alle": "07:00"}


def _data() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios"


def _conf() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "notifiche.json"


class Store:
    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time):
        self.path, self.clock = path or _data() / "notifiche.json", clock
        self.lock = threading.Lock()

    def load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items[:MAX_ITEMS], ensure_ascii=False))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def add(self, app: str, title: str, body: str, urgency: int = 1, desktop: str = "", replaces: int = 0,
            hidden: bool = False) -> dict[str, Any] | None:
        title, body = title.strip()[:300], body.strip()[:2000]
        if not title and not body:
            return None
        with self.lock:
            items = self.load()
            now = self.clock()
            # la stessa notifica ripetuta (un download che avanza, «sostituisce la precedente»): si aggiorna
            same = next((i for i in items[:5] if i["app"] == app and (replaces and i.get("sostituisce") == replaces
                                                                      or (i["titolo"] == title and now - i["quando"] < 60))), None)
            if same:
                items.remove(same)
            n = {"id": f"{int(now * 1000):x}", "app": app or "Sistema", "titolo": title, "testo": body, "quando": int(now),
                 "urgente": urgency >= 2, "app_id": desktop, "letta": False, "nascosta": hidden, "sostituisce": replaces}
            items.insert(0, n)
            self._save(items)
            return n

    def mark_read(self) -> None:
        with self.lock:
            items = self.load()
            if any(not i["letta"] for i in items):
                for i in items:
                    i["letta"] = True
                self._save(items)

    def remove(self, nid: str) -> bool:
        with self.lock:
            items = self.load()
            rest = [i for i in items if i["id"] != nid]
            self._save(rest)
            return len(rest) != len(items)

    def clear(self, app: str = "") -> int:
        with self.lock:
            items = self.load()
            rest = [i for i in items if app and i["app"] != app]
            self._save(rest)
            return len(items) - len(rest)

    def unread(self) -> int:
        return sum(1 for i in self.load() if not i["letta"])


# --- non disturbare -------------------------------------------------------------------------------------
def settings() -> dict[str, Any]:
    conf = dict(DEFAULTS)
    try:
        data = json.loads(_conf().read_text())
        conf.update({k: data[k] for k in DEFAULTS if k in data})
    except (OSError, ValueError):
        pass
    return conf


def save(changes: dict[str, Any]) -> dict[str, Any]:
    conf = settings()
    for k, v in changes.items():
        if k in ("attivo", "nei_giochi", "orari"):
            conf[k] = bool(v)
        elif k in ("dalle", "alle") and isinstance(v, str):
            datetime.strptime(v, "%H:%M")
            conf[k] = v
        elif k == "fino_a":
            conf[k] = str(v)
    _conf().parent.mkdir(parents=True, exist_ok=True)
    _conf().write_text(json.dumps(conf, indent=1))
    return conf


def quiet_for(minutes: int | None, now: datetime | None = None) -> dict[str, Any]:
    """«Non disturbare» per un po' (None: fino a domattina alle 7)."""
    now = now or datetime.now()
    until = now + timedelta(minutes=minutes) if minutes else (now + timedelta(days=1 if now.hour >= 7 else 0)).replace(hour=7, minute=0)
    return save({"fino_a": until.isoformat(timespec="minutes")})


def gaming() -> bool:
    try:
        from . import giochi

        return giochi.flag().exists()
    except Exception:
        return False


def quiet_now(now: datetime | None = None, conf: dict[str, Any] | None = None, playing: Callable[[], bool] = gaming) -> str:
    """Perché adesso è «non disturbare» ("" = non lo è)."""
    now = now or datetime.now()
    conf = conf or settings()
    if conf["attivo"]:
        return "attivo"
    if conf["fino_a"]:
        try:
            if now < datetime.fromisoformat(conf["fino_a"]):
                return "fino alle " + conf["fino_a"][11:16]
        except ValueError:
            pass
    if conf["orari"]:
        a, b = conf["dalle"], conf["alle"]
        t = now.strftime("%H:%M")
        if (a <= t < b) if a < b else (t >= a or t < b):
            return f"dalle {a} alle {b}"
    if conf["nei_giochi"] and playing():
        return "stai giocando"
    return ""


def _run(cmd: list[str]) -> int:
    if not shutil.which(cmd[0]):
        return 127
    try:
        return subprocess.run(cmd, capture_output=True, timeout=5).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def apply_mode(quiet: bool, run: Callable[[list[str]], int] = _run) -> None:
    run(["makoctl", "mode", "-a" if quiet else "-r", MODE])


# --- ascolto delle notifiche (monitor D-Bus) ----------------------------------------------------------------
def listen(store: Store | None = None, on_new: Callable[[dict[str, Any]], None] = lambda n: None) -> None:
    """Si mette in ascolto delle chiamate Notify sul bus di sessione (come dbus-monitor): non le tocca."""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    store = store or Store()
    address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
    conn = Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)

    def on_message(connection: Any, message: Any, incoming: bool) -> Any:
        try:
            if message.get_member() == "Notify" and message.get_interface() == "org.freedesktop.Notifications":
                args = message.get_body().unpack()
                app, replaces, _icon, title, body, _actions, hints = args[:7]
                urgency = int(hints.get("urgency", 1)) if isinstance(hints, dict) else 1
                desktop = str(hints.get("desktop-entry", "")) if isinstance(hints, dict) else ""
                n = store.add(str(app), str(title), str(body), urgency, desktop, int(replaces), hidden=bool(quiet_now()))
                if n:
                    on_new(n)
        except Exception:
            pass
        return None  # un monitor non deve rimandare indietro niente

    conn.add_filter(on_message)
    conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.Monitoring", "BecomeMonitor",
                   GLib.Variant("(asu)", (["type='method_call',interface='org.freedesktop.Notifications',member='Notify'"], 0)),
                   None, Gio.DBusCallFlags.NONE, -1, None)
    while not conn.is_closed():
        time.sleep(30)


def run(store: Store | None = None, every: int = 20) -> None:
    """Il servizio nella shell: ascolta le notifiche e tiene aggiornato «non disturbare»."""
    store = store or Store()

    def listener() -> None:
        while True:
            try:
                listen(store)
            except Exception:
                pass
            time.sleep(10)

    threading.Thread(target=listener, daemon=True).start()
    current: bool | None = None
    while True:
        quiet = bool(quiet_now())
        if quiet != current:
            apply_mode(quiet)
            current = quiet
        time.sleep(every)


def summary(items: list[dict[str, Any]], only_unread: bool = True, limit: int = 40) -> str:
    chosen = [i for i in items if not only_unread or not i["letta"]][:limit]
    if not chosen:
        return "Nessuna notifica nuova." if only_unread else "Nessuna notifica."
    by_app: dict[str, list[dict[str, Any]]] = {}
    for i in chosen:
        by_app.setdefault(i["app"], []).append(i)
    lines = []
    for app, rows in by_app.items():
        lines.append(f"{app} ({len(rows)}):")
        for r in rows[:6]:
            when = datetime.fromtimestamp(r["quando"]).strftime("%d/%m %H:%M")
            lines.append(f"  • {when} {r['titolo']}" + (f" — {r['testo'][:140]}" if r["testo"] else ""))
    return "\n".join(lines)
