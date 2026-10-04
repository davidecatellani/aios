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
    # Legge dati personali (file, memoria): da qui in poi la conversazione è "privata".
    reads_private: bool = False
    # Fa uscire dati dal dispositivo (ricerche, siti): in una conversazione privata
    # l'utente deve vedere e approvare cosa esce, se a chiederlo è il modello.
    sends_out: bool = False
    # Il risultato arriva da un programma di terzi (app tramite l'SDK): è un dato, mai un'istruzione.
    external: bool = False

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
        """Per l'utente: la prima frase della descrizione, mai il nome tecnico dello strumento."""
        first = self.description.split(". ")[0].split(" (")[0].rstrip(".")
        return f"⚙️ {first}…" if first else "⚙️ Un momento…"


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
