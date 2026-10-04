"""La shell di AIOS: la schermata del sistema, al posto del desktop classico.

Non c'è un desktop con icone e menu: c'è la giornata (le carte preparate da Nova), il saluto,
la casella di Nova in basso (scrivi o parla) e un dock con poche app. Le app si aprono a tutto
schermo sopra la schermata; la barra in alto resta sempre visibile e Super riporta qui. Nova vive
solo qui: Super+Spazio o «Nova…» a voce, anche da dentro un'app, riportano alla schermata e la
risposta arriva lì, con le schede dei risultati accanto. Nessun pannello o finestra a parte.

Pezzi:
- ShellApp: il server locale della pagina (localapp.py) con i dati delle carte, l'elenco delle
  app, le finestre aperte e le richieste a Nova (job con stato e conferme, come nel benvenuto);
- GTK/WebKit: due superfici della stessa pagina, «casa» (sotto a tutto, a schermo intero) e la
  barra in alto (layer-shell; compositore Hyprland, image/files/usr/share/aios/hyprland; labwc di riserva).

    aios-shell                avvia la shell (dalla sessione AIOS)
    aios-shell --nova         torna alla schermata con il cursore nella casella di Nova (Super+Spazio)
    aios-shell --casa         torna alla schermata (Super): riduce le app aperte
    aios-shell --voce TESTO   richiesta detta a voce (servizio aios-voce)
    aios-shell --vista NOME[:PARTE]  apre un'app di AIOS (file, foto, musica, video, note, impostazioni:wifi…)
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..localapp import LocalApp, serve
from .apps import register_apps, register_first_steps

PAGE = Path(__file__).with_name("home.html")
APP_ID = "org.aios.Shell"
DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
          "settembre", "ottobre", "novembre", "dicembre"]
# Il dock: le app di AIOS (viste HTML della shell), poi il browser. Gli altri programmi sono in «Tutte».
AIOS_APPS = [
    {"id": "aios:file", "label": "File", "name": "File", "vista": "file", "simbolo": "📁"},
    {"id": "aios:foto", "label": "Foto", "name": "Foto", "vista": "foto", "simbolo": "🖼️"},
    {"id": "aios:musica", "label": "Musica", "name": "Musica", "vista": "musica", "simbolo": "🎵"},
    {"id": "aios:video", "label": "Video", "name": "Video", "vista": "video", "simbolo": "🎬"},
    {"id": "aios:note", "label": "Note", "name": "Note", "vista": "note", "simbolo": "📝"},
    {"id": "aios:impostazioni", "label": "Impostazioni", "name": "Impostazioni", "vista": "impostazioni", "simbolo": "⚙️"},
]
BROWSERS = ["org.mozilla.firefox", "firefox", "org.chromium.Chromium", "chromium-browser", "com.google.Chrome"]
# Le app di sistema di GNOME e Fedora non si mostrano: le loro funzioni le fanno le app di AIOS.
SYSTEM_HIDDEN = re.compile(r"^(org\.gnome\.|gnome-|org\.freedesktop\.|org\.fedoraproject\.|nm-|ibus|yelp|"
                           r"system-config|htop|fedora-|anaconda|liveinst|setroubleshoot|org\.kde\.kdeconnect|"
                           r"kde-connect|kdeconnect|mpv|org\.aios\.|com\.mitchellh\.ptyxis|org\.gnome\.Ptyxis|ptyxis)", re.I)
HIDDEN_APPS = {"org.aios.Shell", "org.aios.Copilot", "org.aios.Welcome", "org.aios.Welcome-autostart"}


# --- app installate -------------------------------------------------------------------------------------
@dataclass
class DesktopApp:
    id: str
    name: str
    icon: str
    exec: str
    keywords: str = ""
    windows: bool = False  # app Windows (Bottles/Wine): segno distintivo nel dock


def app_dirs() -> list[Path]:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    extra = [data_home / "flatpak/exports/share", Path("/var/lib/flatpak/exports/share")]
    return [d / "applications" for d in [data_home, *extra, *map(Path, data_dirs)]]


def read_desktop(path: Path) -> DesktopApp | None:
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment]
    try:
        parser.read(path, encoding="utf-8")
        entry = parser["Desktop Entry"]
    except (configparser.Error, KeyError, UnicodeDecodeError, OSError):
        return None
    if entry.get("Type", "Application") != "Application" or entry.get("NoDisplay", "").lower() == "true" \
            or entry.get("Hidden", "").lower() == "true" or not entry.get("Exec"):
        return None
    shown = entry.get("OnlyShowIn", "")
    if shown and not any(d in shown for d in ("GNOME", "AIOS")):
        return None
    name = entry.get("Name[it]") or entry.get("Name", path.stem)
    keywords = " ".join(filter(None, [entry.get("Keywords[it]", ""), entry.get("Keywords", ""),
                                      entry.get("GenericName[it]", ""), entry.get("Comment[it]", "")]))
    exe = entry.get("Exec", "")
    return DesktopApp(path.stem, name, entry.get("Icon", ""), exe, keywords,
                      windows="bottles" in exe.lower() or "wine" in exe.lower())


def installed_apps(dirs: list[Path] | None = None) -> dict[str, DesktopApp]:
    found: dict[str, DesktopApp] = {}
    for d in dirs if dirs is not None else app_dirs():
        for path in sorted(d.glob("*.desktop")) if d.is_dir() else ():
            if path.stem in found or path.stem in HIDDEN_APPS:
                continue
            if SYSTEM_HIDDEN.match(path.stem) and not str(d).startswith(str(Path.home())) and "flatpak" not in str(d):
                continue
            app = read_desktop(path)
            if app is not None:
                found[path.stem] = app
    return found


def dock_apps(apps: dict[str, DesktopApp]) -> list[dict[str, Any]]:
    dock: list[dict[str, Any]] = [dict(a) for a in AIOS_APPS[:5]]
    browser = next((apps[c] for c in BROWSERS if c in apps), None)
    if browser is not None:
        dock.insert(1, {**asdict(browser), "label": "Internet", "piastrella": "internet"})
    dock.append(dict(AIOS_APPS[5]))
    return dock


def icon_path(name: str, dirs: list[Path] | None = None) -> Path | None:
    """Il file dell'icona di un'app (tema hicolor/Adwaita, pixmaps, o un percorso assoluto)."""
    if not name or "/" in name and not name.startswith("/"):
        return None
    if name.startswith("/"):
        p = Path(name)
        return p if p.is_file() and p.suffix in (".png", ".svg") else None
    if not re.fullmatch(r"[\w.+-]+", name):
        return None
    bases = dirs if dirs is not None else [d.parent / "icons" for d in app_dirs()] + [Path("/usr/share/pixmaps")]
    for base in bases:
        for sub in ("hicolor/scalable/apps", "hicolor/256x256/apps", "hicolor/128x128/apps", "hicolor/96x96/apps",
                    "hicolor/64x64/apps", "hicolor/48x48/apps", "Adwaita/scalable/apps", ""):
            for ext in (".svg", ".png"):
                p = base / sub / f"{name}{ext}"
                if p.is_file():
                    return p
    return None


def launch(app: DesktopApp) -> bool:
    for cmd in (["gtk-launch", app.id], ["gio", "launch", str(next((d / f"{app.id}.desktop" for d in app_dirs()
                                                                     if (d / f"{app.id}.desktop").is_file()), ""))]):
        if shutil.which(cmd[0]) and cmd[-1]:
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return True
            except OSError:
                continue
    return False


# --- finestre: Hyprland (hyprctl) o, di riserva, un compositore wlroots qualsiasi (wlrctl) ---------------------
# Con Hyprland ogni programma ha il suo spazio di lavoro, a tutto schermo sotto la barra: passare da
# uno all'altro scorre con un'animazione; «casa» porta su uno spazio vuoto, dove si vede la giornata.
def hyprland() -> bool:
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))


def hypr_clients(run: Callable[[list[str]], tuple[int, str]] | None = None) -> list[dict[str, Any]]:
    code, out = (run or _run)(["hyprctl", "clients", "-j"])
    try:
        clients = json.loads(out) if code == 0 else []
    except ValueError:
        return []
    return [c for c in clients if isinstance(c, dict) and c.get("mapped", True) and c.get("class") != APP_ID]


def open_windows(run: Callable[[list[str]], tuple[int, str]] | None = None) -> list[dict[str, str]]:
    run = run or _run
    if hyprland():
        ordered = sorted(hypr_clients(run), key=lambda c: c.get("focusHistoryID", 0))
        return [{"app_id": c.get("class") or c.get("initialClass", ""), "title": c.get("title", "")}
                for c in ordered if c.get("class") or c.get("initialClass")]
    code, out = run(["wlrctl", "toplevel", "list"])
    windows = []
    for line in out.splitlines() if code == 0 else []:
        app_id, _, title = line.partition(": ")
        if app_id.strip() and app_id.strip() != APP_ID:
            windows.append({"app_id": app_id.strip(), "title": title.strip()})
    return windows


def minimize_all(run: Callable[[list[str]], tuple[int, str]] | None = None) -> None:
    run = run or _run
    if hyprland():
        run(["hyprctl", "dispatch", "workspace", "empty"])
        return
    for w in open_windows(run):
        run(["wlrctl", "toplevel", "minimize", f"app_id:{w['app_id']}"])


def _class_rule(app_id: str) -> str:
    return f"class:^({re.escape(app_id)})$"


def close_window(app_id: str, run: Callable[[list[str]], tuple[int, str]] | None = None) -> bool:
    if not re.fullmatch(r"[\w.+-]+", app_id) or app_id == APP_ID:
        return False
    if hyprland():
        return (run or _run)(["hyprctl", "dispatch", "closewindow", _class_rule(app_id)])[0] == 0
    return (run or _run)(["wlrctl", "toplevel", "close", f"app_id:{app_id}"])[0] == 0


def focus_window(app_id: str, run: Callable[[list[str]], tuple[int, str]] | None = None) -> bool:
    if not re.fullmatch(r"[\w.+-]+", app_id):
        return False
    if hyprland():
        run = run or _run
        if not any((c.get("class") or c.get("initialClass")) == app_id for c in hypr_clients(run)):
            return False
        return run(["hyprctl", "dispatch", "focuswindow", _class_rule(app_id)])[0] == 0
    return (run or _run)(["wlrctl", "toplevel", "focus", f"app_id:{app_id}"])[0] == 0


def home_visible(run: Callable[[list[str]], tuple[int, str]] | None = None) -> bool:
    """Si vede la schermata principale (nessun programma davanti)?"""
    run = run or _run
    if hyprland():
        code, out = run(["hyprctl", "activewindow", "-j"])
        try:
            active = json.loads(out) if code == 0 and out.strip() else {}
        except ValueError:
            return False
        return not active or not active.get("class") or active.get("class") == APP_ID
    return not open_windows(run)


def own_workspace(address: str, run: Callable[[list[str]], tuple[int, str]] | None = None) -> bool:
    """Una finestra nuova va su uno spazio tutto suo (a tutto schermo), se lo condivide con altre.
    Le finestre flottanti (dialoghi, finestre di scelta file) restano sopra il programma che le ha aperte."""
    run = run or _run
    clients = hypr_clients(run)
    me = next((c for c in clients if str(c.get("address", "")).removeprefix("0x") == address.removeprefix("0x")), None)
    if me is None or me.get("floating") or me.get("class") == APP_ID:
        return False
    ws = (me.get("workspace") or {}).get("id")
    if not any(c is not me and (c.get("workspace") or {}).get("id") == ws and not c.get("floating") for c in clients):
        return False
    return run(["hyprctl", "dispatch", "movetoworkspace", f"empty,address:0x{address.removeprefix('0x')}"])[0] == 0


def hypr_events(handle: Callable[[str, str], None] = lambda event, data: None) -> None:
    """Ascolta gli eventi di Hyprland (socket2): ogni finestra nuova sul suo spazio di lavoro."""
    import socket

    sock_path = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "hypr" / os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "") / ".socket2.sock"
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(str(sock_path))
                buf = b""
                while chunk := s.recv(4096):
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for line in lines:
                        event, _, data = line.decode(errors="replace").partition(">>")
                        if event == "openwindow":
                            threading.Timer(0.15, own_workspace, args=(data.split(",", 1)[0],)).start()
                        handle(event, data)
        except OSError:
            pass
        time.sleep(2)


def _run(cmd: list[str]) -> tuple[int, str]:
    if not shutil.which(cmd[0]):
        return 127, ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return proc.returncode, proc.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


# --- la giornata: le carte --------------------------------------------------------------------------------
def greeting(name: str, now: datetime) -> dict[str, str]:
    hello = "Buongiorno" if 5 <= now.hour < 13 else ("Buon pomeriggio" if now.hour < 18 else "Buonasera")
    name = " ".join(part[:1].upper() + part[1:] for part in name.split())
    return {"saluto": f"{hello}{', ' + name if name else ''}.",
            "data": f"{DAYS[now.weekday()]} {now.day} {MONTHS[now.month - 1]}"}


def day_cards(agenda: Any, now: datetime, recent: list[str]) -> list[dict[str, Any]]:
    """Le carte di oggi: riepilogo, scadenze trovate nei documenti, file da riprendere."""
    cards: list[dict[str, Any]] = []
    try:
        today = [i for i in agenda.day(now.date()) if i.all_day or (i.at and i.at >= now)]
        overdue = agenda.overdue()
        parts = []
        if today:
            parts.append(f"Oggi hai <b>{len(today)} {'impegno' if len(today) == 1 else 'impegni'}</b>: "
                         + ", ".join(_esc(i.title) + ("" if i.all_day else f" alle {i.at:%H:%M}") for i in today[:3]) + ".")
        else:
            parts.append("Oggi non hai impegni in agenda.")
        if overdue:
            parts.append("Rimasto indietro: <b>" + ", ".join(_esc(i.title) for i in overdue[:2]) + "</b>.")
        cards.append({"tipo": "riepilogo", "titolo": "Il tuo riepilogo", "testo": " ".join(parts),
                      "nota": ("Ultimi file: " + ", ".join(_esc(Path(p).name) for p in recent[:2])) if recent else ""})
        for sid, title, due, src in agenda.pending_suggestions()[:2]:
            cards.append({"tipo": "scadenza", "titolo": _esc(title), "testo": f"{_esc(Path(src).name)} · {due:%d/%m/%Y}",
                          "azioni": [{"etichetta": "Aggiungi in agenda", "chiedi": f"aggiungi la scadenza {sid}"},
                                     {"etichetta": "Ignora", "chiedi": f"ignora la scadenza {sid}"}]})
    except Exception:
        cards.append({"tipo": "riepilogo", "titolo": "Il tuo riepilogo", "testo": "Dimmi «buongiorno» per il riepilogo."})
    cards = session_card() + cards + update_cards()
    if recent:
        p = Path(recent[0])
        cards.append({"tipo": "riprendi", "titolo": _esc(p.name), "testo": _esc(str(p.parent).replace(str(Path.home()), "~")),
                      "azioni": [{"etichetta": "Riprendi", "apri": str(p)}]})
    return cards


def save_session_forever(every: float = 30.0) -> None:
    """Ogni mezzo minuto: cosa è aperto (programmi, file, schede), per riprenderlo dopo."""
    from ..sessione import Sessions, snapshot

    sessions = Sessions()
    apps: dict[str, DesktopApp] = {}
    apps_at = 0.0
    while True:
        try:
            if time.monotonic() - apps_at > 600 or not apps:
                apps, apps_at = installed_apps(), time.monotonic()
            sessions.record(snapshot(open_windows(), apps))
        except Exception:
            pass  # il salvataggio non deve mai fermare la shell
        time.sleep(every)


def session_card(booted: float | None = None, now: float | None = None) -> list[dict[str, Any]]:
    """«Riprendi da dove eri»: nella prima mezz'ora dopo l'avvio, finché non si riapre o si rifiuta."""
    try:
        from ..sessione import Sessions, describe
    except Exception:
        return []
    booted = boot_time() if booted is None else booted
    now = now or time.time()
    sessions = Sessions()
    if now - booted > 1800 or sessions.offered(booted):
        return []
    last = sessions.last_session()
    if last is None or last["quando"] > booted:  # niente da prima del riavvio
        return []
    return [{"tipo": "sessione", "titolo": "Riprendi da dove eri",
             "testo": _esc(describe(last)) + ".",
             "azioni": [{"etichetta": "Riapri tutto", "chiedi": "riapri quello che avevo aperto"},
                        {"etichetta": "No, grazie", "chiedi": "non riaprire la sessione"}]}]


def boot_time(stat: Path = Path("/proc/stat")) -> float:
    try:
        for line in stat.read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError):
        pass
    return 0.0


def update_cards(now: float | None = None, booted: float | None = None) -> list[dict[str, Any]]:
    """Aggiornamenti sulla schermata: in corso, pronto (si applica al riavvio), app aggiornate di recente."""
    try:
        from ..updates import load_state
    except Exception:
        return []
    state = load_state()
    now = now or time.time()
    booted = boot_time() if booted is None else booted
    cards: list[dict[str, Any]] = []
    if state.get("in_corso"):
        cards.append({"tipo": "aggiornamento", "titolo": "Sto aggiornando AIOS",
                      "testo": "Scarico in sottofondo: puoi continuare a usare il computer.",
                      "azioni": [{"etichetta": "A che punto è?", "chiedi": "come va l'aggiornamento?"}]})
    ready = state.get("pronto") or {}
    if ready and ready.get("quando", 0) > booted:  # preparato dopo l'ultimo avvio: non ancora applicato
        version = ready.get("versione", "")
        cards.append({"tipo": "aggiornamento",
                      "titolo": "Aggiornamento di sicurezza pronto" if ready.get("sicurezza") else "Nuova versione di AIOS pronta",
                      "testo": (f"AIOS {_esc(version)}: " if version and version != "registro" else "") +
                               "si applica al riavvio, dati e app restano.",
                      "azioni": [{"etichetta": "Riavvia ora", "chiedi": "riavvia per aggiornare"}]})
    apps = state.get("app_aggiornate") or {}
    if apps and now - apps.get("quando", 0) < 86400 and apps.get("nomi"):
        cards.append({"tipo": "aggiornamento", "titolo": "App aggiornate",
                      "testo": _esc(", ".join(apps["nomi"][:4])) + (" e altre" if len(apps["nomi"]) > 4 else "") + "."})
    return cards


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


# --- volume e luminosità: indicatore a comparsa nella barra ---------------------------------------------
def read_volume(run: Callable[[list[str]], tuple[int, str]] | None = None) -> tuple[int, bool] | None:
    """(percentuale, muto) dell'uscita audio, o None."""
    code, out = (run or _run)(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
    m = re.search(r"Volume:\s*([\d.]+)", out) if code == 0 else None
    return (round(float(m.group(1)) * 100), "MUTED" in out) if m else None


def backlight_dir(base: Path = Path("/sys/class/backlight")) -> Path | None:
    found = sorted(p for p in base.glob("*") if (p / "brightness").exists()) if base.is_dir() else []
    return found[0] if found else None


def read_brightness(folder: Path | None = None) -> int | None:
    folder = folder or backlight_dir()
    try:
        return round(int((folder / "brightness").read_text()) * 100 / max(1, int((folder / "max_brightness").read_text())))
    except (OSError, ValueError, TypeError):
        return None


def watch_levels(show: Callable[[str, int, bool], None], stop: threading.Event | None = None) -> None:
    """Chiama show(tipo, livello, muto) quando cambiano volume o luminosità, da tasti, programmi o Nova.
    Volume: eventi di PipeWire (pactl subscribe), niente controlli continui. Luminosità: lettura
    del file del kernel ogni mezzo secondo (costa pochissimo, non avvia programmi)."""
    stop = stop or threading.Event()

    def volume_events() -> None:
        last = read_volume()
        while not stop.is_set():
            if not shutil.which("pactl"):
                return
            try:
                proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            except OSError:
                return
            for line in proc.stdout:  # type: ignore[union-attr]
                if stop.is_set():
                    break
                if "sink" in line and "change" in line:
                    now = read_volume()
                    if now is not None and now != last:
                        last = now
                        show("volume", now[0], now[1])
            proc.kill()
            stop.wait(5)

    threading.Thread(target=volume_events, daemon=True).start()
    last_light = read_brightness()
    while not stop.wait(0.5):
        light = read_brightness()
        if light is not None and light != last_light:
            last_light = light
            show("luminosita", light, False)


def status_bar(read_battery: Callable[[], Any] | None = None, run: Callable[[list[str]], tuple[int, str]] | None = None,
               devices: Callable[[], list[str]] | None = None) -> dict[str, Any]:
    from ..energy import read_battery as rb

    status: dict[str, Any] = {"ai": "AI in locale"}
    try:
        reading = (read_battery or rb)()
        if reading.level is not None:
            status["batteria"] = reading.level
            status["in_carica"] = reading.charging
    except Exception:
        pass
    code, out = (run or _run)(["nmcli", "-t", "-f", "TYPE,STATE,CONNECTION", "device"])
    for line in out.splitlines() if code == 0 else []:
        kind, _, rest = line.partition(":")
        state, _, name = rest.partition(":")
        if state.startswith("connected") and kind in ("wifi", "ethernet", "gsm") or (kind == "bt" and state.startswith("connected")):
            status["rete"] = {"wifi": "📶", "ethernet": "🔌", "gsm": "📱", "bt": "📱"}.get(kind, "🌐")
            status["rete_nome"] = name
            status["rete_tipo"] = kind
            break
    volume = read_volume(run)
    if volume is not None:
        status["volume"], status["muto"] = volume
    try:
        near = (devices or _near_devices)()
        if near:
            status["dispositivi"] = near
    except Exception:
        pass
    return status


def _near_devices() -> list[str]:
    from ..mesh.service import send_command

    reply = send_command({"azione": "stato"}) or {}
    return list(reply.get("vicini", []))


EXAMPLES = ["Ricordami di pagare la bolletta alle 12", "Cerca nei miei file il contratto d'affitto",
            "Metti un po' di musica per lavorare", "Che impegni ho domani?", "Dove mi ero fermato ieri?"]


# --- server della pagina -----------------------------------------------------------------------------------
class ShellApp(LocalApp):
    page = PAGE
    static_dir = PAGE.parent / "static"

    def __init__(self, make_agent: Callable[..., Any] | None = None, clock: Callable[[], datetime] = datetime.now,
                 agenda: Callable[[], Any] | None = None, apps: Callable[[], dict[str, DesktopApp]] = installed_apps,
                 status: Callable[[], dict[str, Any]] = status_bar, recent: Callable[[], list[str]] | None = None):
        super().__init__(make_agent)
        self.clock, self.apps_source, self.status = clock, apps, status
        self._agenda_factory, self._recent = agenda, recent
        self._apps: dict[str, DesktopApp] = {}
        self._apps_at = 0.0
        self.route("GET", r"/api/casa", self._home)
        self.route("GET", r"/api/stato", lambda m, b, q: (200, self.status()))
        self.route("GET", r"/api/app", self._all_apps)
        self.route("GET", r"/api/icona/([\w.+-]+)", self._icon)
        self.route("POST", r"/api/avvia", self._launch)
        self.route("POST", r"/api/apri", self._open_file)
        self.route("GET", r"/api/finestre", lambda m, b, q: (200, {"finestre": open_windows()}))
        self.route("POST", r"/api/finestra", self._focus)
        self.on_home: Callable[[], None] = lambda: None  # la shell GTK chiude le viste aperte
        self.route("POST", r"/api/casa-vai", self._go_home)
        self.route("POST", r"/api/finestra-chiudi", lambda m, b, q: (200, {"ok": close_window(str(b.get("app_id", "")))}))
        self.route("POST", r"/api/parla", self._speak)
        from .. import diario

        self.on_answer = diario.record_exchange
        register_apps(self)
        register_first_steps(self)
        self.route("POST", r"/api/ascolta", self._listen)
        self.route("POST", r"/api/ascolta-si-no", self._listen_yes_no)

    def _go_home(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        threading.Thread(target=minimize_all, daemon=True).start()
        if body.get("chiudi_viste", True):
            self.on_home()
        return 200, {"ok": True}

    def apps(self) -> dict[str, DesktopApp]:
        if time.monotonic() - self._apps_at > 30:  # nuove app installate: si vedono da sole
            self._apps, self._apps_at = self.apps_source(), time.monotonic()
        return self._apps

    def _agenda(self) -> Any:
        if self._agenda_factory is not None:
            return self._agenda_factory()
        from ..agenda import Agenda

        return Agenda()

    def _home(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        from ..welcome import load_profile

        now = self.clock()
        try:
            from ..agenda import recent_files

            recent = (self._recent or recent_files)()
        except Exception:
            recent = []
        return 200, {**greeting(load_profile().get("name", ""), now), "carte": day_cards(self._agenda(), now, recent),
                     "esempi": EXAMPLES, "dock": dock_apps(self.apps()), "stato": self.status()}

    def _all_apps(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        apps = sorted(self.apps().values(), key=lambda a: a.name.lower())
        return 200, {"aios": AIOS_APPS, "app": [asdict(a) for a in apps]}

    def _icon(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        app = self.apps().get(match.group(1))
        path = icon_path(app.icon if app else match.group(1))
        if path is None:
            return 404, {"error": "icona non trovata"}
        import base64

        return 200, {"tipo": "image/svg+xml" if path.suffix == ".svg" else "image/png",
                     "dati": base64.b64encode(path.read_bytes()).decode()}

    def _launch(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        app = self.apps().get(str(body.get("id", "")))
        if app is None:
            return 404, {"error": "app non trovata"}
        if focus_window(app.id):  # già aperta: in primo piano
            return 200, {"ok": True, "gia_aperta": True}
        return 200, {"ok": launch(app)}

    def _open_file(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        path = Path(str(body.get("percorso", ""))).expanduser()
        home = Path.home().resolve()
        try:
            path = path.resolve()
            path.relative_to(home)  # solo i file dell'utente
        except (OSError, ValueError):
            return 403, {"error": "percorso non consentito"}
        if not path.exists():
            return 404, {"error": "file non trovato"}
        from .. import diario

        diario.record_file(path)
        subprocess.Popen(["gio", "open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return 200, {"ok": True}

    def _speak(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        from ..voice import speak

        threading.Thread(target=speak, args=(str(body.get("testo", ""))[:2000],), daemon=True).start()
        return 200, {"ok": True}

    def _listen(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        """Il pulsante del microfono: una frase, trascritta sul computer."""
        from ..voice import Ears, audio_chunks, capture_command

        cmd = capture_command()
        if cmd is None:
            return 503, {"error": "microfono non disponibile"}
        try:
            chunks = audio_chunks(cmd)
            try:
                # col pulsante non serve dire «Nova»: si trascrive quello che si dice
                return 200, {"testo": Ears().transcribe(chunks, require_wake=False) or ""}
            finally:
                chunks.close()
        except Exception as exc:
            return 503, {"error": f"ascolto non riuscito: {exc}"}

    def _listen_yes_no(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        from ..voice import listen_yes_no

        return 200, {"risposta": listen_yes_no()}

    def _focus(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        return 200, {"ok": focus_window(str(body.get("app_id", "")))}


# --- finestre GTK della shell ------------------------------------------------------------------------------
BAR_HEIGHT = 40


def layer_shell() -> Any:
    """gtk4-layer-shell (va caricato prima di Wayland: aios-sessione imposta LD_PRELOAD). None se assente."""
    try:
        import gi

        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell

        return Gtk4LayerShell if Gtk4LayerShell.is_supported() else None
    except (ImportError, ValueError, AttributeError):
        return None


def run_gtk(app: ShellApp, url: str, argv: list[str]) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("WebKit", "6.0")
    from gi.repository import Gio, GLib, Gtk, WebKit

    gtk_app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    state: dict[str, Any] = {}  # le finestre, create una volta sola da build()
    keep_alive: list[Any] = []

    def view_for(part: str) -> Any:
        settings = WebKit.Settings()
        settings.set_enable_developer_extras(False)
        settings.set_enable_write_console_messages_to_stdout(True)  # errori della pagina nel registro (journalctl)
        settings.set_enable_back_forward_navigation_gestures(False)
        view = WebKit.WebView(settings=settings)
        view.set_background_color(_rgba("rgba(0,0,0,0)" if part != "casa" else "#0A2A3A"))
        view.load_uri(f"{url}&parte={part}")
        view.connect("decide-policy", _only_local)
        return view

    def window(title: str, part: str) -> Any:
        win = Gtk.ApplicationWindow(application=gtk_app, title=title)
        win.set_decorated(False)
        view = view_for(part)
        win.set_child(view)
        return win, view

    def build() -> None:
        if "home" in state:
            return
        ls = layer_shell()
        home, home_view = window("AIOS", "casa")
        bar, bar_view = window("AIOS barra", "barra")
        if ls is not None:
            edges = (ls.Edge.TOP, ls.Edge.BOTTOM, ls.Edge.LEFT, ls.Edge.RIGHT)
            ls.init_for_window(home)  # la giornata, sotto alle app
            ls.set_layer(home, ls.Layer.BOTTOM)
            ls.set_namespace(home, "aios-casa")
            for e in edges:
                ls.set_anchor(home, e, True)
            ls.set_keyboard_mode(home, ls.KeyboardMode.ON_DEMAND)
            ls.init_for_window(bar)  # la barra, sempre visibile: le app si aprono sotto
            ls.set_layer(bar, ls.Layer.TOP)
            ls.set_namespace(bar, "aios-barra")
            for e in (ls.Edge.TOP, ls.Edge.LEFT, ls.Edge.RIGHT):
                ls.set_anchor(bar, e, True)
            ls.auto_exclusive_zone_enable(bar)
            bar.set_default_size(-1, BAR_HEIGHT)
            bar.present()
        else:  # senza il compositore di AIOS (es. dentro GNOME): finestre normali
            home.set_default_size(1280, 800)
        home.present()
        state.update(home=home, home_view=home_view, bar=bar, bar_view=bar_view)

        def show_level(kind: str, level: int, muted: bool) -> None:
            code = f"window.mostraLivello && window.mostraLivello({json.dumps(kind)}, {int(level)}, {'true' if muted else 'false'})"
            GLib.idle_add(run_js, bar_view if ls is not None else home_view, code)

        threading.Thread(target=watch_levels, args=(show_level,), daemon=True).start()
        app.on_home = lambda: GLib.idle_add(run_js, home_view, "window.chiudiVista && window.chiudiVista()")

    def run_js(view: Any, code: str) -> None:
        view.evaluate_javascript(code, -1, None, None, None, None, None)

    def to_home(ask: str = "", by_voice: bool = False) -> None:
        """Nova vive solo nella schermata principale: ci si torna (i programmi restano aperti, dietro)
        e lì si scrive o si risponde, con le schede accanto. Niente pannelli o finestre a parte."""
        build()
        if not home_visible():
            threading.Thread(target=minimize_all, daemon=True).start()
        state["home"].present()
        if ask:
            run_js(state["home_view"], "window.chiudiVista && window.chiudiVista(); "
                   f"window.novaChiedi && window.novaChiedi({json.dumps(ask)}, {'true' if by_voice else 'false'})")
        else:
            run_js(state["home_view"], "window.chiudiVista && window.chiudiVista(); window.novaFocus && window.novaFocus()")

    def handle(args: list[str]) -> bool:
        build()
        if "--nova" in args:
            to_home()
        elif "--casa" in args:
            threading.Thread(target=minimize_all, daemon=True).start()
            run_js(state["home_view"], "window.chiudiVista && window.chiudiVista(); window.novaFocus && window.novaFocus()")
        elif "--vista" in args and args.index("--vista") + 1 < len(args):
            # da Nova: «apri le impostazioni del Wi-Fi», «mostrami le foto»
            what = args[args.index("--vista") + 1].split(":")
            if re.fullmatch(r"[a-z]+", what[0]):
                threading.Thread(target=minimize_all, daemon=True).start()
                run_js(state["home_view"], f"window.apriVista && window.apriVista({json.dumps(what[0])}"
                       + (f", {json.dumps(what[1])})" if len(what) > 1 else ")"))
        elif "--voce" in args and args.index("--voce") + 1 < len(args):
            to_home(args[args.index("--voce") + 1], by_voice=True)
        return False

    def command_line(application: Any, cmdline: Any) -> int:
        args = list(cmdline.get_arguments()[1:])
        GLib.idle_add(handle, args)
        return 0

    def dark_theme(*_: Any) -> None:
        """AIOS è scuro di base; chiaro solo se l'utente lo sceglie («tema chiaro», gsettings prefer-light).
        Le pagine seguono subito il cambio (prefers-color-scheme di WebKit)."""
        gtk_settings = Gtk.Settings.get_default()
        try:
            iface = Gio.Settings.new("org.gnome.desktop.interface")
        except Exception:  # schema assente: resta scuro
            gtk_settings.set_property("gtk-application-prefer-dark-theme", True)
            return

        def apply(*_: Any) -> None:
            gtk_settings.set_property("gtk-application-prefer-dark-theme", iface.get_string("color-scheme") != "prefer-light")

        iface.connect("changed::color-scheme", apply)
        keep_alive.append(iface)  # tenuto vivo per ricevere i cambi (non in «state»: lì solo le finestre)
        apply()

    gtk_app.connect("startup", dark_theme)
    gtk_app.connect("command-line", command_line)
    gtk_app.hold()  # la shell resta viva anche senza finestre in primo piano
    return gtk_app.run(argv)


def _rgba(color: str) -> Any:
    from gi.repository import Gdk

    rgba = Gdk.RGBA()
    rgba.parse(color)
    return rgba


def _only_local(view: Any, decision: Any, kind: Any) -> bool:
    """La pagina della shell non naviga altrove: i link esterni si aprono nel browser."""
    from gi.repository import WebKit

    if kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
        uri = decision.get_navigation_action().get_request().get_uri()
        if not uri.startswith("http://127.0.0.1:"):
            decision.ignore()
            subprocess.Popen(["xdg-open", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
    return False


def make_agent_for_shell(confirm: Callable[..., bool]) -> Any:
    from ..__main__ import make_agent

    return make_agent(confirm)


def forward(args: list[str]) -> bool:
    """Se la shell è già in esecuzione, le passa il comando (--nova, --casa, --voce)."""
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gio, Gtk

        app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        app.register(None)
        if app.get_is_remote():
            app.run([sys.argv[0], *args])
            return True
    except Exception:
        pass
    return False


def power_profile_for(charging: bool | None, level: int | None) -> str | None:
    """A batteria: risparmio; in carica: bilanciato. None se non c'è batteria."""
    if level is None:  # PC fisso
        return None
    return "balanced" if charging else "power-saver"


def auto_power_profile(read: Callable[[], Any] | None = None, run: Callable[[list[str]], tuple[int, str]] | None = None,
                       interval: float = 60.0, stop: threading.Event | None = None) -> None:
    """Cambia il profilo energetico (powerprofilesctl) quando si stacca o si attacca la corrente."""
    from ..energy import read_battery

    read, run, stop = read or read_battery, run or _run, stop or threading.Event()
    current = None
    while not stop.is_set():
        try:
            r = read()
            wanted = power_profile_for(r.charging, r.level)
            if wanted and wanted != current and run(["powerprofilesctl", "set", wanted])[0] == 0:
                current = wanted
                if hyprland():  # a batteria niente sfocatura e ombre (la scheda video lavora meno)
                    on = "false" if wanted == "power-saver" else "true"
                    run(["hyprctl", "--batch", f"keyword decoration:blur:enabled {on} ; keyword decoration:shadow:enabled {on}"])
        except Exception:
            pass
        stop.wait(interval)


def main(argv: list[str] | None = None) -> int:
    # gtk4-layer-shell serve solo a questo processo (aios-sessione lo carica con LD_PRELOAD): i programmi
    # aperti da qui non devono ereditarlo, o quelli GTK3 come Firefox si bloccano all'avvio.
    os.environ.pop("LD_PRELOAD", None)
    args = list(sys.argv[1:] if argv is None else argv)
    if args and forward(args):
        return 0
    threading.Thread(target=auto_power_profile, daemon=True).start()
    from ..diario import WindowWatcher

    threading.Thread(target=WindowWatcher(open_windows).run, daemon=True).start()  # il diario dei programmi
    if hyprland():
        threading.Thread(target=hypr_events, daemon=True).start()  # ogni programma sul suo spazio
    threading.Thread(target=save_session_forever, daemon=True).start()  # da riaprire al riavvio o altrove
    app = ShellApp(make_agent_for_shell)
    server, url = serve(app)
    return run_gtk(app, url, [sys.argv[0], *args])


if __name__ == "__main__":
    sys.exit(main())
