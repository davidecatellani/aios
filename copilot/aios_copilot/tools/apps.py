"""Cerca, installa, rimuove e avvia applicazioni.

Le app si installano preferibilmente da Flathub (sandbox, nessun impatto sul sistema
base); i pacchetti di sistema (apt) sono la seconda scelta e passano da pkexec/polkit.
"""

from __future__ import annotations

import configparser
import os
import re
from pathlib import Path

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


def find_apps(runner: Runner, query: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if runner.has("flatpak"):
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
                return ["flatpak", "install", "-y", "--noninteractive", "flathub", app_id]
            return ["flatpak", "uninstall", "-y", "--noninteractive", app_id]
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

    def launch_app(name: str) -> str:
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
            "launch_app",
            "Apre un'applicazione installata, dato il suo nome (es. 'Firefox') o ID Flatpak.",
            params(name="Nome o ID dell'applicazione"),
            launch_app,
        ),
    ]
