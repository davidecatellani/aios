"""Avvio del copilota.

    aios-copilot                     finestra grafica (predefinito)
    aios-copilot --cli               conversazione nel terminale
    aios-copilot "installa vlc"      singola richiesta nel terminale
    aios-copilot --voce "testo"      richiesta detta a voce (servizio aios-voce)
"""

from __future__ import annotations

import argparse
import sys
import threading
from typing import Any

from .agent import Agent, Confirm
from .fastpath import FastPath
from .fileindex import FileIndex
from .learning import History
from .llm import LLMError, make_client
from .multilingual import load_config, neural_router
from .semantic import SemanticRouter, default_router
from .status import describe_call
from .agenda import Agenda
from .tools import Runner, Tool, apps, default_tools, files
from .tools import agenda as agenda_tools
from .tools import mail as mail_tools
from .tools import taste as taste_tools
from .tools import ai as ai_tools
from .tools import organize as organize_tools
from .tools import themes as theme_tools
from .tools import phone as phone_tools
from .tools import identity as identity_tools
from .tools import updates as update_tools
from . import sdk
from .tools import documents as document_tools
from .tools import energy as energy_tools
from .tools import voice as voice_tools


def make_agent(confirm: Confirm, model: str | None = None, allowed: frozenset[str] | None = None) -> Agent:
    """Il copilota. `allowed` limita gli strumenti (es. richieste arrivate dal telefono: mesh/delegate.py)."""
    runner = Runner()
    index: list[FileIndex] = []  # aperto alla prima ricerca, non all'avvio

    def get_index() -> FileIndex:
        if not index:
            index.append(FileIndex())
        return index[0]

    agenda: list[Agenda] = []

    def get_agenda() -> Agenda:
        if not agenda:
            agenda.append(Agenda())
        return agenda[0]

    lazy: dict[str, object] = {}

    def once(key: str, factory):
        if key not in lazy:
            lazy[key] = factory()
        return lazy[key]

    def mail_store():
        from .mail.store import MailStore

        return once("mail", MailStore)

    def send(to, subject, body, reply_to):
        from .mail.service import send_with_account

        return send_with_account(mail_store(), to, subject, body, reply_to)

    def has_accounts() -> bool:
        from .mail.client import load_accounts

        return bool(load_accounts())

    def subs():
        from .subscriptions import Subscriptions

        return once("subs", Subscriptions)

    def catalog():
        from .recommend import Catalog

        return once("catalog", Catalog)

    def profile():
        from .recommend import Profile

        return once("profile", Profile)

    def library():
        from .organize import Library

        return once("library", Library)

    def device():
        from .hardware import detect

        return once("device", detect)

    def installed_models() -> list[str]:
        try:
            from .multilingual import installed_models as tags

            return sorted(tags())
        except Exception:
            return []

    def downloads():
        from .models import Queue

        return Queue()

    from . import engines

    def model_hint() -> str | None:
        from .models import weekly_hint

        try:
            return weekly_hint(device(), installed_models())
        except Exception:
            return None

    def user_name() -> str:
        from .welcome import load_profile

        return load_profile().get("name", "")

    llm = make_client(model)
    tools = [*default_tools(runner), *files.make_tools(get_index), *agenda_tools.make_tools(get_agenda, user_name, extras=lambda: [model_hint()]),
         *mail_tools.make_tools(mail_store, send, has_accounts), *taste_tools.make_tools(subs, catalog, profile),
         *ai_tools.make_management_tools(device, installed_models, downloads),
         *ai_tools.make_capability_tools(engines.available()), *organize_tools.make_tools(library),
         *theme_tools.make_tools(ask_llm=lambda prompt: llm.chat([{"role": "user", "content": prompt}], []).get("content", ""),
                                 runner=runner),
         *phone_tools.make_tools(runner), *identity_tools.make_tools(user_name=user_name),
         *update_tools.make_tools(runner), *sdk.make_tools(),
         *document_tools.make_tools(get_index, runner), *energy_tools.make_tools(), *voice_tools.make_tools(runner)]
    if allowed is not None:
        tools = [t for t in tools if t.name in allowed]
    agent = Agent(
        llm,
        tools,
        confirm,
        history=History().record,
        routers=[
            agenda_tools.AgendaRouter(),  # livello 0: promemoria, appuntamenti, riepilogo
            mail_tools.MailRouter(),  # livello 0: posta
            taste_tools.TasteRouter(),  # livello 0: abbonamenti e consigli
            ai_tools.ModelsRouter(),  # livello 0: modelli AI del dispositivo
            phone_tools.PhoneRouter(),  # livello 0: telefono e chiamate
            identity_tools.IdentityRouter(),  # livello 0: identità e sincronizzazione
            update_tools.UpdatesRouter(),  # livello 0: aggiornamenti del sistema
            energy_tools.EnergyRouter(),  # livello 0: batteria (decide Nova)
            voice_tools.VoiceRouter(),  # livello 0: ascolto a voce
            sdk.AppsRouter(),  # livello 0: frasi delle abilità offerte dalle app
            FastPath(find_apps=lambda query: apps.find_apps(runner, query)),  # livello 0
            organize_tools.OrganizeRouter(),  # livello 0: raccolte e riordino (dopo le cartelle)
            document_tools.DocumentsRouter(),  # livello 0: «fammi vedere la bolletta…», dieta, lista della spesa
            theme_tools.ThemesRouter(),  # livello 0: temi
            semantic_router(),  # livello 1: italiano e inglese, < 1 ms
            *multilingual_router(),  # livello 1b: tutte le lingue, se configurato
        ],
    )
    # Il modello si prepara mentre l'utente scrive la prima richiesta.
    threading.Thread(target=agent.warmup, daemon=True).start()
    return agent


def semantic_router() -> SemanticRouter:
    return default_router()


def multilingual_router() -> list[SemanticRouter]:
    config = load_config()
    if config is None:
        return []
    try:
        return [neural_router(config)]
    except Exception as exc:  # Ollama spento o modello rimosso: si va avanti senza
        print(f"Riconoscimento multilingue non disponibile ({exc}).", file=sys.stderr)
        return []


def terminal_confirm(tool: Tool, args: dict[str, Any], warning: str | None = None) -> bool:
    if warning:
        print(f"  🔒 {warning}")
    answer = input(f"  Confermi? {describe_call(tool, args)} [s/N] ")
    return answer.strip().lower() in ("s", "si", "sì", "y", "yes")


def print_event(kind: str, data: dict[str, Any]) -> None:
    if kind == "routed":
        names = ["0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "1", "1 multilingue"]
        level = names[data["level"]] if data["level"] < len(names) else data["level"]
        print(f"  ⚡ capito al livello {level}, senza modello AI")
    elif kind == "tool_call" and not data["tool"].requires_confirmation:
        print(f"  {describe_call(data['tool'], data['args'])}")


def run_cli(agent: Agent, request: str | None) -> int:
    requests = [request] if request else None
    while True:
        try:
            text = requests.pop(0) if requests is not None else input("\n› ")
        except (EOFError, KeyboardInterrupt, IndexError):
            return 0
        if not text.strip():
            continue
        try:
            print("\n" + agent.ask(text, print_event))
        except LLMError as exc:
            print(f"\n{exc}", file=sys.stderr)
            return 1
        if requests is not None and not requests:
            return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aios-copilot", description="Nova, l'assistente AI di AIOS")
    parser.add_argument("request", nargs="?", help="richiesta singola da eseguire nel terminale")
    parser.add_argument("--cli", action="store_true", help="usa il terminale invece della finestra")
    parser.add_argument("--model", help="modello Ollama da usare (default: $AIOS_MODEL)")
    parser.add_argument("--voce", help="richiesta detta a voce (dal servizio aios-voce): risposta anche a voce")
    args = parser.parse_args(argv)

    if args.cli or args.request:
        return run_cli(make_agent(terminal_confirm, args.model), args.request)

    try:
        from . import ui_gtk
    except (ImportError, ValueError) as exc:
        print(f"Interfaccia grafica non disponibile ({exc}); uso il terminale.", file=sys.stderr)
        return run_cli(make_agent(terminal_confirm, args.model), None)
    return ui_gtk.run(lambda confirm: make_agent(confirm, args.model), args.voce)


if __name__ == "__main__":
    sys.exit(main())
