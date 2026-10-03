"""Definizione degli strumenti che il copilota può usare."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., str]
    # Le azioni che modificano il sistema devono essere approvate dall'utente.
    requires_confirmation: bool = False

    def schema(self) -> dict[str, Any]:
        """Descrizione dello strumento nel formato tool-calling di Ollama/OpenAI."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def describe_call(self, args: dict[str, Any]) -> str:
        shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"{self.name}({shown})"


def params(required: list[str] | None = None, **props: str | tuple[str, list[str]]) -> dict[str, Any]:
    """Schema JSON compatto: ogni proprietà è una stringa con la sua descrizione,
    oppure una coppia (descrizione, valori ammessi)."""

    def prop(spec: str | tuple[str, list[str]]) -> dict[str, Any]:
        if isinstance(spec, tuple):
            return {"type": "string", "description": spec[0], "enum": spec[1]}
        return {"type": "string", "description": spec}

    return {
        "type": "object",
        "properties": {k: prop(v) for k, v in props.items()},
        "required": required if required is not None else list(props),
    }


@dataclass
class Runner:
    """Esegue comandi di sistema; sostituibile nei test."""

    timeout: int = 600
    which: Callable[[str], str | None] = field(default=shutil.which)

    def run(self, cmd: list[str]) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout
            )
        except FileNotFoundError:
            return 127, f"comando non trovato: {cmd[0]}"
        except subprocess.TimeoutExpired:
            return 124, f"tempo scaduto: {' '.join(cmd)}"
        return proc.returncode, (proc.stdout + proc.stderr).strip()

    def spawn(self, cmd: list[str]) -> None:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def has(self, program: str) -> bool:
        return self.which(program) is not None

    def first(self, *candidates: list[str]) -> tuple[int, str] | None:
        """Esegue il primo comando il cui programma è installato; None se nessuno lo è."""
        for cmd in candidates:
            if self.has(cmd[0]):
                return self.run(cmd)
        return None
