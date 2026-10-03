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
}


def describe_call(tool: Tool, args: dict[str, Any]) -> str:
    template = TEMPLATES.get(tool.name)
    if template is None:
        return tool.describe_call(args)
    try:
        return template.format(**args)
    except (KeyError, IndexError):
        return tool.describe_call(args)
