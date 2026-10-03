"""Avvio del copilota.

    aios-copilot                     finestra grafica (predefinito)
    aios-copilot --cli               conversazione nel terminale
    aios-copilot "installa vlc"      singola richiesta nel terminale
"""

from __future__ import annotations

import argparse
import sys
import threading
from typing import Any

from .agent import Agent, Confirm
from .fastpath import FastPath
from .llm import LLMError, OllamaClient
from .status import describe_call
from .tools import Runner, Tool, apps, default_tools


def make_agent(confirm: Confirm, model: str | None = None) -> Agent:
    runner = Runner()
    agent = Agent(
        OllamaClient(model=model),
        default_tools(runner),
        confirm,
        fastpath=FastPath(find_apps=lambda query: apps.find_apps(runner, query)),
    )
    # Il modello si prepara mentre l'utente scrive la prima richiesta.
    threading.Thread(target=agent.warmup, daemon=True).start()
    return agent


def terminal_confirm(tool: Tool, args: dict[str, Any]) -> bool:
    answer = input(f"  Confermi? {describe_call(tool, args)} [s/N] ")
    return answer.strip().lower() in ("s", "si", "sì", "y", "yes")


def print_event(kind: str, data: dict[str, Any]) -> None:
    if kind == "tool_call" and not data["tool"].requires_confirmation:
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
    parser = argparse.ArgumentParser(prog="aios-copilot", description="Copilota AI di AIOS")
    parser.add_argument("request", nargs="?", help="richiesta singola da eseguire nel terminale")
    parser.add_argument("--cli", action="store_true", help="usa il terminale invece della finestra")
    parser.add_argument("--model", help="modello Ollama da usare (default: $AIOS_MODEL)")
    args = parser.parse_args(argv)

    if args.cli or args.request:
        return run_cli(make_agent(terminal_confirm, args.model), args.request)

    try:
        from . import ui_gtk
    except (ImportError, ValueError) as exc:
        print(f"Interfaccia grafica non disponibile ({exc}); uso il terminale.", file=sys.stderr)
        return run_cli(make_agent(terminal_confirm, args.model), None)
    return ui_gtk.run(lambda confirm: make_agent(confirm, args.model))


if __name__ == "__main__":
    sys.exit(main())
