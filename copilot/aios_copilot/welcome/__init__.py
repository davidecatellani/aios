"""Benvenuto di AIOS: il primo avvio è già una conversazione con il copilota.

La pagina (index.html) è servita da un piccolo server locale che la collega
all'agente vero: quello che l'utente scrive durante il benvenuto viene eseguito
davvero, con lo stato in tempo reale e le conferme per le azioni importanti.

Server, sicurezza e richieste al copilota: vedi localapp.py.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from ..agent import Agent
from ..localapp import LocalApp, open_window, serve

PAGE = Path(__file__).with_name("index.html")


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios"


def profile_path() -> Path:
    return config_dir() / "profile.json"


def load_profile() -> dict[str, Any]:
    try:
        data = json.loads(profile_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_profile(**changes: Any) -> dict[str, Any]:
    profile = {**load_profile(), **changes}
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    return profile


def clean_name(name: Any) -> str:
    """Nome dell'utente: solo lettere, spazi, apostrofi e trattini, max 40 caratteri."""
    if not isinstance(name, str):
        return ""
    return re.sub(r"[^\w\s'’-]|\d|_", "", name).strip()[:40]


class WelcomeApp(LocalApp):
    """Il benvenuto: profilo dell'utente più le richieste al copilota."""

    page = PAGE

    def __init__(self, make_agent: Callable[[Callable[..., bool]], Agent]):
        super().__init__(make_agent)
        self.route("GET", r"/api/state", lambda m, b, q: (200, {"profile": load_profile()}))
        self.route("POST", r"/api/profile", self._profile)
        self.route("POST", r"/api/finish", self._finish)

    def _profile(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        changes = {}
        if "name" in body:
            changes["name"] = clean_name(body["name"])
        if body.get("lang") in ("it", "en"):
            changes["lang"] = body["lang"]
        return 200, {"profile": save_profile(**changes)}

    def _finish(self, match: Any, body: dict[str, Any], query: dict[str, str]) -> tuple[int, Any]:
        save_profile(welcome_done=True)
        self.finished.set()
        return 200, {"ok": True}


def main(argv: list[str] | None = None) -> int:
    import argparse

    from ..__main__ import make_agent

    parser = argparse.ArgumentParser(prog="aios-welcome", description="Benvenuto di AIOS")
    parser.add_argument("--first-run", action="store_true", help="non fare nulla se il benvenuto è già stato completato")
    parser.add_argument("--no-window", action="store_true", help="stampa solo l'indirizzo (es. per aprirlo da un altro dispositivo locale)")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)

    if args.first_run and load_profile().get("welcome_done"):
        return 0
    app = WelcomeApp(make_agent)
    server, url = serve(app, args.port)
    try:
        if args.no_window:
            print(url, flush=True)
            app.finished.wait()
        else:
            open_window(url, app.finished, "Benvenuto in AIOS", "org.aios.Welcome")
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0
