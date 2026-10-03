"""L'agente: dialoga con il modello e esegue gli strumenti che richiede."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable

from .llm import ChatModel
from .tools import Tool

SYSTEM_PROMPT = """\
Sei il Copilota di AIOS, il sistema operativo in cui l'utente fa tutto parlando con te.
Oggi è {today}.

Regole:
- Rispondi nella lingua dell'utente, in modo breve e concreto.
- Agisci invece di spiegare: se l'utente vuole installare, aprire o cercare qualcosa,
  usa gli strumenti.
- Per informazioni aggiornate (notizie, orari, prezzi, meteo, versioni) usa search_web
  e, se serve, read_webpage; indica sempre le fonti (URL).
- Per installare un'app: prima search_apps, poi install_app con id e source trovati.
  Preferisci source='flatpak'. Se l'utente chiede un programma Windows, proponi
  un'alternativa Linux equivalente oppure Bottles (com.usebottles.bottles), che
  permette di eseguire molte app Windows.
- Le azioni importanti vengono confermate dall'utente: se rifiuta, non insistere.
- Non inventare risultati: se uno strumento fallisce, dillo e proponi un'alternativa.
"""

Confirm = Callable[[Tool, dict[str, Any]], bool]
OnEvent = Callable[[str, dict[str, Any]], None]


class Agent:
    def __init__(
        self,
        model: ChatModel,
        tools: list[Tool],
        confirm: Confirm,
        max_steps: int = 8,
    ):
        self.model = model
        self.tools = {t.name: t for t in tools}
        self.confirm = confirm
        self.max_steps = max_steps
        self.reset()

    def reset(self) -> None:
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(today=date.today().isoformat())}
        ]

    def ask(self, text: str, on_event: OnEvent | None = None) -> str:
        """Elabora una richiesta dell'utente e restituisce la risposta finale."""
        emit = on_event or (lambda kind, data: None)
        self.messages.append({"role": "user", "content": text})
        schemas = [t.schema() for t in self.tools.values()]

        for _ in range(self.max_steps):
            reply = self.model.chat(self.messages, schemas)
            calls = reply.get("tool_calls") or []
            self.messages.append(
                {"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls}
                if calls
                else {"role": "assistant", "content": reply.get("content") or ""}
            )
            if not calls:
                return reply.get("content") or ""
            for call in calls:
                name = call.get("function", {}).get("name", "")
                result = self._run_tool(name, call.get("function", {}).get("arguments"), emit)
                self.messages.append({"role": "tool", "tool_name": name, "content": result})

        return "Mi sono fermato: la richiesta richiedeva troppi passaggi. Puoi riformularla?"

    def _run_tool(self, name: str, raw_args: Any, emit: OnEvent) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"Strumento sconosciuto: {name}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
        except (ValueError, TypeError):
            return f"Argomenti non validi per {name}: {raw_args!r}"

        emit("tool_call", {"tool": tool, "args": args})
        if tool.requires_confirmation and not self.confirm(tool, args):
            result = "L'utente ha rifiutato questa azione."
        else:
            try:
                result = tool.func(**args)
            except TypeError as exc:
                result = f"Argomenti errati per {name}: {exc}"
            except Exception as exc:
                result = f"Errore durante {name}: {exc}"
        emit("tool_result", {"tool": tool, "result": result})
        return result
