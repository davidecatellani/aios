"""Applica un tema a tutto il sistema: app GTK/libadwaita, GNOME, KDE e app di AIOS.

Nei file di configurazione GTK il tema occupa solo un blocco tra due marcatori: le
personalizzazioni dell'utente fuori dal blocco restano intatte. Il tema precedente
viene ricordato per poter tornare indietro.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .themes import Palette, Theme, default_theme, hls, installed, load, save, wallpaper_files
from .tools.base import Runner

START, END = "/* AIOS-TEMA-INIZIO */", "/* AIOS-TEMA-FINE */"
# Colori d'accento di GNOME (dalla versione 47): si sceglie il più vicino.
GNOME_ACCENTS = {"blue": 0.6, "teal": 0.5, "green": 0.36, "yellow": 0.14, "orange": 0.07, "red": 0.0, "pink": 0.92,
                 "purple": 0.76, "slate": None}


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def state_path() -> Path:
    return config_dir() / "aios" / "theme.json"


def current_id() -> str:
    try:
        return json.loads(state_path().read_text()).get("current", "aios")
    except (OSError, ValueError):
        return "aios"


def css_path() -> Path:
    return config_dir() / "aios" / "theme.css"


def current_css() -> str:
    try:
        return css_path().read_text()
    except OSError:
        return ""


def gtk_block(p: Palette) -> str:
    return "\n".join([
        START,
        f"@define-color accent_color {p.accent};", f"@define-color accent_bg_color {p.accent};",
        f"@define-color accent_fg_color {p.on_accent};", f"@define-color window_bg_color {p.bg};",
        f"@define-color window_fg_color {p.text};", f"@define-color view_bg_color {p.surface};",
        f"@define-color view_fg_color {p.text};", f"@define-color headerbar_bg_color {p.bg};",
        f"@define-color headerbar_fg_color {p.text};", f"@define-color card_bg_color {p.surface};",
        f"@define-color card_fg_color {p.text};", f"@define-color popover_bg_color {p.surface};",
        f"@define-color popover_fg_color {p.text};", f"@define-color sidebar_bg_color {p.bg};",
        END,
    ])


def write_block(path: Path, block: str | None) -> None:
    """Sostituisce (o toglie, con None) solo il blocco di AIOS nel file."""
    try:
        text = path.read_text()
    except OSError:
        text = ""
    text = re.sub(re.escape(START) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S)
    if block:
        text = text.rstrip("\n") + ("\n\n" if text.strip() else "") + block + "\n"
    if text.strip() or path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def nearest_gnome_accent(color: str) -> str:
    hue, light, sat = hls(color)
    if sat < 0.15:
        return "slate"
    return min((k for k, v in GNOME_ACCENTS.items() if v is not None),
               key=lambda k: min(abs(hue - GNOME_ACCENTS[k]), 1 - abs(hue - GNOME_ACCENTS[k])))


def kde_colors(theme: Theme, p: Palette) -> str:
    def rgb(h: str) -> str:
        return ",".join(str(int(h[i:i + 2], 16)) for i in (1, 3, 5))

    lines = [f"[General]\nName=AIOS {theme.name}\nColorScheme=AIOS{theme.id}\n"]
    for section in ("Window", "View", "Button", "Selection", "Tooltip", "Header"):
        bg = p.accent if section == "Selection" else (p.surface if section in ("View", "Button", "Tooltip") else p.bg)
        fg = p.on_accent if section == "Selection" else p.text
        lines.append(f"[Colors:{section}]\nBackgroundNormal={rgb(bg)}\nBackgroundAlternate={rgb(bg)}\n"
                     f"ForegroundNormal={rgb(fg)}\nForegroundInactive={rgb(p.muted)}\nDecorationFocus={rgb(p.accent)}\n"
                     f"DecorationHover={rgb(p.accent2)}\n")
    return "\n".join(lines)


def apply(theme: Theme, runner: Runner | None = None) -> list[str]:
    """Applica il tema; restituisce cosa è stato aggiornato."""
    runner = runner or Runner()
    if load(theme.id) is None:
        save(theme)
    done = []
    css_path().parent.mkdir(parents=True, exist_ok=True)
    css_path().write_text(theme.css())
    done.append("app di AIOS")

    dark_pref = False
    if runner.has("gsettings"):
        code, out = runner.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
        dark_pref = code == 0 and "dark" in out
    palette = theme.dark if dark_pref else theme.light
    for version in ("gtk-4.0", "gtk-3.0"):
        write_block(config_dir() / version / "gtk.css", gtk_block(palette))
    done.append("app GTK")

    light_wp, dark_wp = wallpaper_files(theme)
    if runner.has("gsettings"):
        if light_wp:
            runner.run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", light_wp.as_uri()])
            runner.run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", (dark_wp or light_wp).as_uri()])
            runner.run(["gsettings", "set", "org.gnome.desktop.background", "picture-options", "zoom"])
        runner.run(["gsettings", "set", "org.gnome.desktop.interface", "accent-color", nearest_gnome_accent(theme.light.accent)])
        done.append("GNOME")
    if runner.has("plasma-apply-colorscheme"):
        schemes = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "color-schemes"
        schemes.mkdir(parents=True, exist_ok=True)
        (schemes / f"AIOS{theme.id}.colors").write_text(kde_colors(theme, palette))
        runner.run(["plasma-apply-colorscheme", f"AIOS{theme.id}"])
        if light_wp and runner.has("plasma-apply-wallpaperimage"):
            runner.run(["plasma-apply-wallpaperimage", str(dark_wp if dark_pref and dark_wp else light_wp)])
        done.append("KDE")

    previous = current_id()
    state_path().write_text(json.dumps({"current": theme.id, "previous": previous if previous != theme.id else
                                        _read_state().get("previous", "aios")}))
    return done


def _read_state() -> dict:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def previous() -> Theme:
    pid = _read_state().get("previous", "aios")
    return load(pid) or default_theme()


def reset(runner: Runner | None = None) -> None:
    """Torna al tema predefinito e toglie i blocchi di AIOS dai file GTK."""
    apply(default_theme(), runner)
    for version in ("gtk-4.0", "gtk-3.0"):
        write_block(config_dir() / version / "gtk.css", None)


def find(name: str) -> Theme | None:
    name = name.lower().strip()
    for t in [default_theme(), *installed()]:
        if name in (t.id, t.name.lower()) or name in t.name.lower():
            return t
    return None
