"""L'agente: dialoga con il modello e esegue gli strumenti che richiede."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable, Protocol, Sequence

from .fastpath import Intent
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

REFUSED = "L'utente ha rifiutato questa azione."

Confirm = Callable[[Tool, dict[str, Any]], bool]
OnEvent = Callable[[str, dict[str, Any]], None]


class Router(Protocol):
    """Livello veloce: riconosce una richiesta senza l'LLM, o restituisce None."""

    def match(self, text: str) -> Intent | None: ...


class Agent:
    def __init__(
        self,
        model: ChatModel,
        tools: list[Tool],
        confirm: Confirm,
        max_steps: int = 8,
        routers: Sequence[Router] = (),
    ):
        self.model = model
        self.tools = {t.name: t for t in tools}
        self.confirm = confirm
        self.max_steps = max_steps
        # In ordine, dal più rapido al più flessibile; l'LLM è l'ultima risorsa.
        self.routers = list(routers)
        self.reset()

    def reset(self) -> None:
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(today=date.today().isoformat())}
        ]

    def ask(self, text: str, on_event: OnEvent | None = None) -> str:
        """Elabora una richiesta dell'utente e restituisce la risposta finale."""
        emit = on_event or (lambda kind, data: None)
        self.messages.append({"role": "user", "content": text})

        for level, router in enumerate(self.routers):
            intent = router.match(text)
            if intent is not None and intent.tool in self.tools:
                return self._run_intent(intent, level, emit)

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

    def _run_intent(self, intent: Intent, level: int, emit: OnEvent) -> str:
        emit("routed", {"intent": intent, "level": level})
        result = self._run_tool(intent.tool, intent.args, emit)
        answer = "Va bene, annullato." if result == REFUSED else result
        # Resta nella cronologia: l'LLM avrà il contesto per le richieste successive.
        self.messages.append({"role": "assistant", "content": answer})
        return answer

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
            result = REFUSED
        else:
            try:
                result = tool.func(**args)
            except TypeError as exc:
                result = f"Argomenti errati per {name}: {exc}"
            except Exception as exc:
                result = f"Errore durante {name}: {exc}"
        emit("tool_result", {"tool": tool, "result": result})
        return result

    def warmup(self) -> None:
        """Prepara il modello in background, così la prima risposta è già veloce."""
        try:
            self.model.warmup(self.messages[:1], [t.schema() for t in self.tools.values()])
        except Exception:
            pass  # facoltativo: se il modello non è raggiungibile se ne accorgerà ask()
