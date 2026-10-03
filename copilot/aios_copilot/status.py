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
    "search_files": "🗂️ Cerco nei tuoi file: {query}",
    "read_file": "📄 Leggo {path}",
    "exclude_folder": "🚫 Escludo {path}",
    "add_reminder": "🔔 Promemoria: {what}",
    "add_event": "📅 In agenda: {title}",
    "list_agenda": "📅 Guardo l'agenda: {period}",
    "daily_briefing": "☀️ Preparo il riepilogo",
    "complete_reminder": "✅ Segno come fatto: {query}",
    "delete_agenda_item": "🗑️ Eliminare dall'agenda: {query}",
    "resolve_suggestion": "📌 Scadenza proposta n. {number}: {accept}",
    "mail_overview": "✉️ Controllo la posta",
    "search_mail": "✉️ Cerco nelle mail: {query}",
    "read_mail": "✉️ Leggo la mail {mail_id}",
    "categorize_mail": "🗂️ Mail di {who} → {category}",
    "list_subscriptions": "💳 Guardo i tuoi abbonamenti",
    "set_subscription": "💳 Abbonamento {service}: {active}",
    "recommend": "🍿 Cerco qualcosa per te: {kind}",
    "rate": "⭐ {title}: piaciuto? {liked}",
    "suggest_models": "🧠 Guardo cosa può fare questo dispositivo",
    "restore_model": "🧠 Torno al modello precedente: {capability}",
    "list_collections": "🗂️ Guardo le tue raccolte",
    "show_collection": "🗂️ Raccolta: {what}",
    "tidy_plan": "🧹 Preparo il riordino: {folder}",
    "tidy_apply": "🧹 Eseguire il riordino preparato",
    "tidy_undo": "↩️ Annullare l'ultimo riordino",
    "cleanup_suggestions": "🧹 Cerco cosa si può eliminare",
    "create_theme": "🎨 Creo il tema: {description}",
    "create_theme_from_image": "🎨 Creo un tema da {path}",
    "remix_theme": "🎨 Tema {change}",
    "apply_theme": "🎨 Applico il tema {name}",
    "previous_theme": "🎨 Torno al tema di prima",
    "list_themes": "🎨 I tuoi temi",
    "market_search": "🛍️ Cerco temi nel market: {query}",
    "market_install": "🛍️ Installare dal market: {theme_id}",
    "export_theme": "📦 Esporto il tema",
    "install_models": "🧠 Scaricare e attivare i modelli: {which}",
    "models_status": "🧠 Stato dei modelli",
    "describe_image": "👁️ Guardo {path}",
    "look_at_screen": "👁️ Guardo lo schermo",
    "read_aloud": "🔊 Leggo ad alta voce",
    "transcribe_audio": "🎙️ Trascrivo {path}",
    "create_image": "🎨 Creo un'immagine: {prompt}",
}


# Valori tecnici degli strumenti, mostrati all'utente in italiano.
VALUES = {
    "up": "su", "down": "giù", "mute": "muto", "unmute": "riattiva", "dark": "scuro",
    "light": "chiaro", "on": "acceso", "off": "spento", "wifi": "Wi-Fi", "bluetooth": "Bluetooth",
    "play": "riproduci", "pause": "pausa", "play-pause": "riproduci/pausa", "next": "successivo",
    "previous": "precedente", "suspend": "sospendi", "poweroff": "spegni", "reboot": "riavvia",
}


def describe_call(tool: Tool, args: dict[str, Any]) -> str:
    if tool.name == "send_email":  # nella conferma si vede cosa parte davvero
        body = str(args.get("body", ""))
        preview = body[:280] + ("…" if len(body) > 280 else "")
        return f"✉️ Inviare a {args.get('to', '?')} — «{args.get('subject', '')}»\n{preview}"
    template = TEMPLATES.get(tool.name)
    if template is None:
        return tool.describe_call(args)
    shown = {k: VALUES.get(v, v) if isinstance(v, str) else v for k, v in args.items()}
    try:
        return template.format(**shown)
    except (KeyError, IndexError):
        return tool.describe_call(args)
