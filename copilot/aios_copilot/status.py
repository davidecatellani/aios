"""Descrizioni leggibili delle azioni del copilota, mostrate all'utente."""

from __future__ import annotations

from typing import Any

from .tools import Tool

TEMPLATES = {
    "search_web": "🔎 Cerco su internet: {query}",
    "read_webpage": "📄 Leggo {url}",
    "search_apps": "🧩 Cerco applicazioni: {query}",
    "install_app": "⬇️ Installare {app_id} ({source})",
    "remove_app": "🗑️ Disinstallare {app_id} ({source})",
    "launch_app": "🚀 Apro {name}",
    "open_location": "📂 Apro {target}",
    "system_info": "💻 Controllo il sistema",
    "set_volume": "🔊 Volume: {action}",
    "set_brightness": "🔆 Luminosità: {action}",
    "set_theme": "🎨 Tema: {mode}",
    "set_radio": "📶 {device}: {state}",
    "media_control": "🎵 Musica: {action}",
    "take_screenshot": "📸 Screenshot",
    "lock_screen": "🔒 Blocco lo schermo",
    "power": "⏻ Energia: {action}",
}


# Valori tecnici degli strumenti, mostrati all'utente in italiano.
VALUES = {
    "up": "su", "down": "giù", "mute": "muto", "unmute": "riattiva", "dark": "scuro",
    "light": "chiaro", "on": "acceso", "off": "spento", "wifi": "Wi-Fi", "bluetooth": "Bluetooth",
    "play": "riproduci", "pause": "pausa", "play-pause": "riproduci/pausa", "next": "successivo",
    "previous": "precedente", "suspend": "sospendi", "poweroff": "spegni", "reboot": "riavvia",
}


def describe_call(tool: Tool, args: dict[str, Any]) -> str:
    template = TEMPLATES.get(tool.name)
    if template is None:
        return tool.describe_call(args)
    shown = {k: VALUES.get(v, v) if isinstance(v, str) else v for k, v in args.items()}
    try:
        return template.format(**shown)
    except (KeyError, IndexError):
        return tool.describe_call(args)
