"""Informazioni sul sistema e apertura di file, cartelle e indirizzi."""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

from .base import Runner, Tool, params


def _meminfo() -> dict[str, int]:
    info = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, value = line.partition(":")
            info[key] = int(value.split()[0]) // 1024  # MiB
    except (OSError, ValueError, IndexError):
        pass
    return info


def make_tools(runner: Runner | None = None) -> list[Tool]:
    runner = runner or Runner()

    def system_info() -> str:
        mem = _meminfo()
        disk = shutil.disk_usage(Path.home())
        lines = [
            f"Sistema: {platform.system()} {platform.release()} ({platform.machine()})",
            f"CPU: {os.cpu_count()} core",
            f"Disco (home): {disk.free // 2**30} GiB liberi su {disk.total // 2**30} GiB",
        ]
        if mem:
            lines.append(
                f"Memoria: {mem.get('MemAvailable', 0)} MiB disponibili su {mem.get('MemTotal', 0)} MiB"
            )
        return "\n".join(lines)

    def open_location(target: str) -> str:
        if not target.startswith(("http://", "https://")):
            path = Path(target).expanduser()
            if not path.exists():
                return f"Il percorso {path} non esiste."
            target = str(path)
        if not runner.has("xdg-open"):
            return "xdg-open non disponibile su questo sistema."
        runner.spawn(["xdg-open", target])
        return f"Aperto {target}."

    return [
        Tool(
            "system_info",
            "Restituisce informazioni sul dispositivo: sistema, CPU, memoria e spazio su disco.",
            params(),
            system_info,
        ),
        Tool(
            "open_location",
            "Apre un file, una cartella (es. '~/Scaricati') o un indirizzo web con "
            "l'applicazione predefinita.",
            params(target="Percorso o URL da aprire"),
            open_location,
        ),
    ]
