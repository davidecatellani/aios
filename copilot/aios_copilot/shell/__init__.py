"""La shell di AIOS: la schermata del sistema, al posto del desktop classico.

Non c'è un desktop con icone e menu: c'è la giornata (le carte preparate da Nova), il saluto,
la casella di Nova in basso (scrivi o parla) e un dock con poche app. Le app si aprono a tutto
schermo sopra la schermata; la barra in alto resta sempre visibile e Super riporta qui. Sopra
qualsiasi app, Super+Spazio (o «Nova…» a voce) apre il pannello di Nova, che conosce il contesto.

Pezzi:
- ShellApp: il server locale della pagina (localapp.py) con i dati delle carte, l'elenco delle
  app, le finestre aperte e le richieste a Nova (job con stato e conferme, come nel benvenuto);
- GTK/WebKit: due finestre della stessa pagina, «casa» (sotto a tutto, a schermo intero) e
  «pannello» (Nova sopra le app); il compositore (labwc, image/files/usr/share/aios/labwc) le
  riconosce dal titolo e le tiene al loro posto.

    aios-shell                avvia la shell (dalla sessione AIOS)
    aios-shell --nova         mostra/nasconde il pannello di Nova (Super+Spazio)
    aios-shell --casa         torna alla schermata (Super): riduce le app aperte
    aios-shell --voce TESTO   richiesta detta a voce (servizio aios-voce)
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

PAGE = Path(__file__).with_name("home.html")
APP_ID = "org.aios.Shell"
DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
          "settembre", "ottobre", "novembre", "dicembre"]
# Il dock: le app di tutti i giorni (la prima presente per ogni posto), poi quelle che usi di più.
DOCK = [
    ("File", ["org.gnome.Nautilus", "nautilus"]),
    ("Internet", ["org.mozilla.firefox", "firefox", "org.chromium.Chromium", "chromium-browser"]),
    ("Documenti", ["org.libreoffice.LibreOffice.writer", "libreoffice-writer", "org.gnome.TextEditor"]),
    ("Musica", ["org.gnome.Music", "io.bassi.Amberol", "rhythmbox"]),
    ("Video", ["org.gnome.Showtime", "org.gnome.Totem", "vlc", "org.videolan.VLC"]),
    ("Foto", ["org.gnome.Loupe", "org.gnome.eog", "org.gnome.Photos"]),
    ("Impostazioni", ["org.gnome.Settings", "gnome-control-center"]),
]
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
            app = read_desktop(path)
            if app is not None:
                found[path.stem] = app
    return found


def dock_apps(apps: dict[str, DesktopApp]) -> list[dict[str, Any]]:
    dock = []
    for label, choices in DOCK:
        app = next((apps[c] for c in choices if c in apps), None)
        if app is not None:
            dock.append({**asdict(app), "label": label})
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


# --- finestre (compositore wlroots: wlrctl) ---------------------------------------------------------------
def open_windows(run: Callable[[list[str]], tuple[int, str]] | None = None) -> list[dict[str, str]]:
    run = run or _run
    code, out = run(["wlrctl", "toplevel", "list"])
    windows = []
    for line in out.splitlines() if code == 0 else []:
        app_id, _, title = line.partition(": ")
        if app_id.strip() and app_id.strip() != APP_ID:
            windows.append({"app_id": app_id.strip(), "title": title.strip()})
    return windows


def minimize_all(run: Callable[[list[str]], tuple[int, str]] | None = None) -> None:
    run = run or _run
    for w in open_windows(run):
        run(["wlrctl", "toplevel", "minimize", f"app_id:{w['app_id']}"])


def focus_window(app_id: str, run: Callable[[list[str]], tuple[int, str]] | None = None) -> bool:
    if not re.fullmatch(r"[\w.+-]+", app_id):
        return False
    return (run or _run)(["wlrctl", "toplevel", "focus", f"app_id:{app_id}"])[0] == 0


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
    if recent:
        p = Path(recent[0])
        cards.append({"tipo": "riprendi", "titolo": _esc(p.name), "testo": _esc(str(p.parent).replace(str(Path.home()), "~")),
                      "azioni": [{"etichetta": "Riprendi", "apri": str(p)}]})
    return cards


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


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
            break
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


EXAMPLES = ["🔔 Ricordami di pagare la bolletta alle 12", "🗂️ Cerca nei miei file il contratto d'affitto",
            "🎵 Metti un po' di musica per lavorare", "📅 Che impegni ho domani?"]


# --- server della pagina -----------------------------------------------------------------------------------
class ShellApp(LocalApp):
    page = PAGE

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
        self.route("POST", r"/api/casa-vai", lambda m, b, q: (threading.Thread(target=minimize_all, daemon=True).start(),
                                                               (200, {"ok": True}))[1])
        self.route("POST", r"/api/parla", self._speak)
        self.route("POST", r"/api/ascolta", self._listen)
        self.route("POST", r"/api/ascolta-si-no", self._listen_yes_no)

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
        return 200, {"app": [asdict(a) for a in apps]}

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
                return 200, {"testo": Ears().transcribe(chunks)}
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
PANEL_WIDTH = 480


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
    state: dict[str, Any] = {}

    def view_for(part: str) -> Any:
        settings = WebKit.Settings()
        settings.set_enable_developer_extras(False)
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
        if state:
            return
        ls = layer_shell()
        home, home_view = window("AIOS", "casa")
        bar, _ = window("AIOS barra", "barra")
        panel, panel_view = window("Nova", "pannello")
        panel.set_hide_on_close(True)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda c, kv, code, st: _escape(panel, kv))
        panel.add_controller(keys)
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
            ls.init_for_window(panel)  # Nova sopra a tutto, sul lato destro
            ls.set_layer(panel, ls.Layer.OVERLAY)
            ls.set_namespace(panel, "aios-nova")
            for e in (ls.Edge.TOP, ls.Edge.BOTTOM, ls.Edge.RIGHT):
                ls.set_anchor(panel, e, True)
            ls.set_margin(panel, ls.Edge.TOP, 8)
            ls.set_margin(panel, ls.Edge.BOTTOM, 8)
            ls.set_margin(panel, ls.Edge.RIGHT, 8)
            ls.set_keyboard_mode(panel, ls.KeyboardMode.ON_DEMAND)
            panel.set_default_size(PANEL_WIDTH, -1)
            bar.present()
        else:  # senza il compositore di AIOS (es. dentro GNOME): finestre normali
            home.set_default_size(1280, 800)
            panel.set_default_size(PANEL_WIDTH, 720)
        home.present()
        state.update(home=home, home_view=home_view, bar=bar, panel=panel, panel_view=panel_view)

    def run_js(view: Any, code: str) -> None:
        view.evaluate_javascript(code, -1, None, None, None, None, None)

    def toggle_panel(ask: str = "") -> None:
        build()
        panel = state["panel"]
        if ask:
            panel.present()
            run_js(state["panel_view"], f"window.novaChiedi && window.novaChiedi({json.dumps(ask)}, true)")
        elif panel.get_visible():
            panel.set_visible(False)
        else:
            panel.present()
            run_js(state["panel_view"], "window.novaFocus && window.novaFocus()")

    def handle(args: list[str]) -> bool:
        build()
        if "--nova" in args:
            toggle_panel()
        elif "--casa" in args:
            state["panel"].set_visible(False)
            threading.Thread(target=minimize_all, daemon=True).start()
            run_js(state["home_view"], "window.novaFocus && window.novaFocus()")
        elif "--voce" in args and args.index("--voce") + 1 < len(args):
            toggle_panel(args[args.index("--voce") + 1])
        return False

    def command_line(application: Any, cmdline: Any) -> int:
        args = list(cmdline.get_arguments()[1:])
        GLib.idle_add(handle, args)
        return 0

    gtk_app.connect("command-line", command_line)
    gtk_app.hold()  # la shell resta viva anche senza finestre in primo piano
    return gtk_app.run(argv)


def _escape(panel: Any, keyval: int) -> bool:
    from gi.repository import Gdk

    if keyval == Gdk.KEY_Escape:
        panel.set_visible(False)
        return True
    return False


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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and forward(args):
        return 0
    app = ShellApp(make_agent_for_shell)
    server, url = serve(app)
    return run_gtk(app, url, [sys.argv[0], *args])


if __name__ == "__main__":
    sys.exit(main())
