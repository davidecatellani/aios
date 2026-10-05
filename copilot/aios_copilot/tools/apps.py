"""Cerca, installa, rimuove e avvia applicazioni.

Le app si installano preferibilmente da Flathub (sandbox, nessun impatto sul sistema
base); i pacchetti di sistema (apt) sono la seconda scelta e passano da pkexec/polkit.
"""

from __future__ import annotations

import configparser
import os
import re
from pathlib import Path
from typing import Any

from .base import Runner, Tool, params

APP_ID = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){2,}$")
PACKAGE = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
SOURCES = ("flatpak", "system")


def parse_flatpak_search(output: str) -> list[dict[str, str]]:
    apps = []
    for line in output.splitlines():
        cols = line.split("\t")
        if len(cols) >= 2 and APP_ID.match(cols[1].strip()):
            apps.append(
                {
                    "name": cols[0].strip(),
                    "id": cols[1].strip(),
                    "description": cols[2].strip() if len(cols) > 2 else "",
                    "source": "flatpak",
                }
            )
    return apps


def parse_apt_search(output: str) -> list[dict[str, str]]:
    apps = []
    for line in output.splitlines():
        name, sep, desc = line.partition(" - ")
        if sep and PACKAGE.match(name.strip()):
            apps.append(
                {"name": name.strip(), "id": name.strip(), "description": desc.strip(), "source": "system"}
            )
    return apps


def desktop_dirs() -> list[Path]:
    data_home = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    extra = [
        str(Path.home() / ".local/share/flatpak/exports/share"),
        "/var/lib/flatpak/exports/share",
    ]
    return [Path(d) / "applications" for d in [data_home, *data_dirs, *extra]]


def find_desktop_entry(name: str, dirs: list[Path] | None = None) -> Path | None:
    """Trova il file .desktop il cui nome (o ID) corrisponde meglio alla richiesta."""
    wanted = name.lower().strip()
    partial = None
    for directory in dirs if dirs is not None else desktop_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.desktop")):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(path, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if entry.get("NoDisplay", "false").lower() == "true":
                continue
            names = {entry.get("Name", "").lower(), path.stem.lower()}
            if wanted in names:
                return path
            if partial is None and any(wanted in n for n in names if n):
                partial = path
    return partial


def exec_command(desktop_file: Path) -> list[str]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read(desktop_file, encoding="utf-8")
    exec_line = parser["Desktop Entry"].get("Exec", "")
    # Rimuove i segnaposto della specifica freedesktop (%u, %F, ...).
    return [a for a in exec_line.split() if not re.fullmatch(r"%[a-zA-Z]", a)]


FLATHUB = "https://dl.flathub.org/repo/flathub.flatpakrepo"


def ensure_flathub(runner: Runner) -> None:
    """Flathub per l'utente: le app si installano senza password di amministratore (AIOS è immutabile)."""
    code, out = runner.run(["flatpak", "remotes", "--user", "--columns=name"])
    if code == 0 and "flathub" in out.split():
        return
    runner.run(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB])
    runner.run(["flatpak", "update", "--user", "--appstream", "flathub"])


def find_apps(runner: Runner, query: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if runner.has("flatpak"):
        ensure_flathub(runner)
        _, out = runner.run(["flatpak", "search", "--columns=name,application,description", query])
        found += parse_flatpak_search(out)[:8]
    if runner.has("apt-cache"):
        _, out = runner.run(["apt-cache", "search", "--names-only", query])
        found += parse_apt_search(out)[:5]
    return found


def make_tools(runner: Runner | None = None) -> list[Tool]:
    runner = runner or Runner()

    def search_apps(query: str) -> str:
        found = find_apps(runner, query)
        if not found:
            return f"Nessuna applicazione trovata per '{query}'."
        return "\n".join(
            f"- {a['name']} [id={a['id']}, source={a['source']}]: {a['description']}"
            for a in found
        )

    def _package_command(action: str, app_id: str, source: str) -> list[str] | str:
        if source not in SOURCES:
            return f"Sorgente non valida: usa una tra {', '.join(SOURCES)}."
        if source == "flatpak":
            if not APP_ID.match(app_id):
                return f"ID Flatpak non valido: {app_id}"
            if not runner.has("flatpak"):
                return "Flatpak non è installato su questo sistema."
            if action == "install":
                ensure_flathub(runner)
                return ["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", app_id]
            return ["flatpak", "uninstall", "--user", "-y", "--noninteractive", app_id]
        if not PACKAGE.match(app_id):
            return f"Nome pacchetto non valido: {app_id}"
        if not runner.has("apt-get"):
            return "Nessun gestore di pacchetti di sistema supportato (apt) trovato."
        return ["pkexec", "apt-get", action, "-y", app_id]

    def install_app(app_id: str, source: str) -> str:
        cmd = _package_command("install", app_id, source)
        if isinstance(cmd, str):
            return cmd
        code, out = runner.run(cmd)
        tail = "\n".join(out.splitlines()[-5:])
        return f"Installato {app_id}." if code == 0 else f"Installazione fallita ({code}):\n{tail}"

    def remove_app(app_id: str, source: str) -> str:
        cmd = _package_command("remove", app_id, source)
        if isinstance(cmd, str):
            return cmd
        code, out = runner.run(cmd)
        tail = "\n".join(out.splitlines()[-5:])
        return f"Rimosso {app_id}." if code == 0 else f"Rimozione fallita ({code}):\n{tail}"

    def already_open(name: str) -> str | None:
        """«apri Firefox» con Firefox già aperto: si porta davanti quello, non se ne apre un altro."""
        from . import windows as win

        if not win.in_aios_session():
            return None
        from .. import shell as sh

        try:
            w = win.match_window(name.rsplit(".", 1)[-1] if APP_ID.match(name) else name, sh.open_windows())
        except Exception:
            return None
        if w is not None and sh.focus_window(w["app_id"]):
            return f"{w['title'] or w['app_id']} era già aperto: eccolo."
        return None

    def launch_app(name: str) -> str:
        shown = already_open(name)
        if shown:
            return shown
        if APP_ID.match(name) and runner.has("flatpak"):
            code, _ = runner.run(["flatpak", "info", name])
            if code == 0:
                runner.spawn(["flatpak", "run", name])
                return f"Avviato {name}."
        entry = find_desktop_entry(name)
        if entry is not None:
            if runner.has("gtk-launch"):
                runner.spawn(["gtk-launch", entry.stem])
            else:
                runner.spawn(exec_command(entry))
            return f"Avviato {entry.stem}."
        if PACKAGE.match(name) and runner.has(name):
            runner.spawn([name])
            return f"Avviato {name}."
        return f"Non trovo un'applicazione chiamata '{name}'. Prova a cercarla con search_apps."

    def install_game(titolo: str) -> str:
        return GameInstaller(runner).install(titolo)

    def install_software(nome: str) -> str:
        return GameInstaller(runner).install_any(nome)

    def play_game(titolo: str) -> str:
        return GameInstaller(runner).play(titolo)

    source_desc = "'flatpak' (preferita) oppure 'system', come indicato da search_apps"
    return [
        Tool(
            "search_apps",
            "Cerca applicazioni installabili (Flathub e pacchetti di sistema).",
            params(query="Nome o funzione dell'app, in inglese (es. 'video editor')"),
            search_apps,
        ),
        Tool(
            "install_app",
            "Installa un'applicazione. Usa l'id e la source restituiti da search_apps.",
            params(app_id="ID dell'app o nome del pacchetto", source=source_desc),
            install_app,
            requires_confirmation=True,
        ),
        Tool(
            "remove_app",
            "Disinstalla un'applicazione.",
            params(app_id="ID dell'app o nome del pacchetto", source=source_desc),
            remove_app,
            requires_confirmation=True,
        ),
        Tool(
            "install_software",
            "Installa un programma o un gioco dal suo nome (es. «Visual Studio Code», «Spotify», «SuperTuxKart»): trova "
            "da solo la fonte giusta (Flathub, poi Steam per i giochi) e installa tutto. Preferiscilo a install_app.",
            params(nome="Il nome del programma o del gioco", required=["nome"]),
            install_software,
        ),
        Tool(
            "install_game",
            "Installa un gioco dal titolo, da solo: da Flathub se c'è, altrimenti con Steam (installa anche Steam se "
            "manca). Usalo quando l'utente vuole un gioco: non mandargli link.",
            params(titolo="Il titolo del gioco", required=["titolo"]),
            install_game,
        ),
        Tool(
            "play_game",
            "Avvia un gioco installato (Flathub o Steam) dal titolo.",
            params(titolo="Il titolo del gioco", required=["titolo"]),
            play_game,
        ),
        Tool(
            "launch_app",
            "Apre un'applicazione installata, dato il suo nome (es. 'Firefox') o ID Flatpak.",
            params(name="Nome o ID dell'applicazione"),
            launch_app,
        ),
    ]


# --- giochi: Flathub o Steam, senza che l'utente debba fare niente -----------------------------------------
STEAM_ID = "com.valvesoftware.Steam"
# come le persone chiamano i programmi → come si chiamano su Flathub
ALIASES = {"vscode": "Visual Studio Code", "vs code": "Visual Studio Code", "code": "Visual Studio Code",
           "chrome": "Google Chrome", "google chrome": "Google Chrome", "edge": "Microsoft Edge", "word": "OnlyOffice",
           "office": "LibreOffice", "photoshop": "GIMP", "teams": "Teams for Linux", "whatsapp": "WhatsApp Desktop",
           "obs": "OBS Studio", "vlc": "VLC", "telegram": "Telegram Desktop", "minecraft": "Prism Launcher"}
STEAM_SEARCH = "https://store.steampowered.com/api/storesearch/?term={q}&l=italian&cc=IT"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _http(url: str) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AIOS"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


class GameInstaller:
    """Installa e avvia giochi: prima Flathub (installazione completa e automatica), poi Steam (che fa il resto
    lui, con il tuo account: i giochi gratuiti o già tuoi si scaricano subito, quelli a pagamento si comprano lì)."""

    def __init__(self, runner: Runner, fetch: Any = _http):
        self.runner, self.fetch = runner, fetch

    def flathub(self, title: str) -> dict[str, str] | None:
        """La corrispondenza migliore su Flathub: nome uguale, poi che inizia così, poi che lo contiene."""
        want = _norm(ALIASES.get(_norm(title), title))
        found = [a for a in find_apps(self.runner, want) if a.get("source") == "flatpak"]
        if not found and want != _norm(title):
            found = [a for a in find_apps(self.runner, title) if a.get("source") == "flatpak"]
        for test in (lambda n: n == want, lambda n: n.startswith(want + " ") or want.startswith(n + " "),
                     lambda n: len(want) > 3 and want in n):
            hit = next((a for a in found if test(_norm(a["name"])) or _norm(a["id"]).endswith(want.replace(" ", ""))), None)
            if hit:
                return hit
        return None

    def install_any(self, name: str) -> str:
        """Un programma o un gioco dal nome: Flathub (tutto automatico), altrimenti Steam per i giochi."""
        name = name.strip().strip("«»\"'")
        if not name:
            return "Cosa installo?"
        if self.runner.has("flatpak"):
            app = self.flathub(name)
            if app is not None:
                code, _ = self.runner.run(["flatpak", "info", app["id"]])
                if code == 0:
                    from .base import offer

                    offer(f"apri {app['name']}")
                    return f"{app['name']} è già installato. Vuoi che lo apra?"
                code, out = self.runner.run(["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", app["id"]])
                if code == 0:
                    from .base import offer

                    offer(f"apri {app['name']}")
                    return f"Installato {app['name']}. Vuoi che lo apra?"
                return f"L'installazione di {app['name']} non è riuscita: {out.strip().splitlines()[-1] if out.strip() else 'errore sconosciuto'}."
        return self.install(name)

    def steam(self, title: str) -> dict[str, Any] | None:
        import json
        import urllib.parse

        try:
            data = json.loads(self.fetch(STEAM_SEARCH.format(q=urllib.parse.quote(title))))
        except Exception:
            return None
        items = data.get("items") or []
        want = _norm(title)
        best = next((i for i in items if _norm(i.get("name", "")) == want), None) or (items[0] if items else None)
        if best is None:
            return None
        price = best.get("price") or {}
        return {"id": int(best["id"]), "nome": best.get("name", title),
                "prezzo": (price.get("final", 0) / 100) if price else 0.0}

    def _steam_ready(self) -> bool:
        code, _ = self.runner.run(["flatpak", "info", "--user", STEAM_ID])
        if code == 0:
            return True
        code, _ = self.runner.run(["flatpak", "info", STEAM_ID])
        if code == 0:
            return True
        ensure_flathub(self.runner)
        code, _ = self.runner.run(["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", STEAM_ID])
        return code == 0

    def install(self, title: str) -> str:
        title = title.strip()
        if not title:
            return "Quale gioco?"
        if self.runner.has("flatpak"):
            app = self.flathub(title)
            if app is not None:
                code, out = self.runner.run(["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", app["id"]])
                if code == 0:
                    from .base import offer

                    offer(f"avvia {app['name']}")
                    return f"Installato {app['name']} (gratuito, da Flathub). Vuoi che lo apra?"
        game = self.steam(title)
        if game is None:
            return f"Non trovo «{title}» né su Flathub né su Steam."
        if not self.runner.has("flatpak") or not self._steam_ready():
            return f"«{game['nome']}» è su Steam, ma non riesco a installare Steam su questo PC."
        # Steam fa da sé: un gioco gratuito o già tuo lo scarica subito, uno da comprare ne mostra prima la pagina
        self.runner.spawn(["flatpak", "run", STEAM_ID, f"steam://install/{game['id']}"])
        if game["prezzo"]:
            price = f"{game['prezzo']:.2f}".replace(".", ",")
            return (f"Ho chiesto a Steam di installare «{game['nome']}» ({price} €): se ce l'hai già lo scarica subito, "
                    "altrimenti ti mostra la pagina per comprarlo e poi lo scarica da solo.")
        return (f"Installo «{game['nome']}» con Steam (è gratuito): lo scarica da solo. La prima volta Steam ti chiede "
                "di entrare con il tuo account.")

    def play(self, title: str) -> str:
        if self.runner.has("flatpak"):
            app = self.flathub(title)
            if app is not None:
                code, _ = self.runner.run(["flatpak", "info", app["id"]])
                if code == 0:
                    self.runner.spawn(["flatpak", "run", app["id"]])
                    return f"Avvio {app['name']}."
        game = self.steam(title)
        if game is None:
            return f"Non trovo «{title}»."
        self.runner.spawn(["flatpak", "run", STEAM_ID, f"steam://rungameid/{game['id']}"])
        return f"Avvio «{game['nome']}» con Steam (se non è ancora installato, Steam lo scarica prima)."
