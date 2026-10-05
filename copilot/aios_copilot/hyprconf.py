"""Le scelte dell'utente che Hyprland deve ritrovare al prossimo avvio: schermi, mouse e tastiera, accessibilità.

Ogni parte (monitor.py, dispositivi.py, accessibilita.py) tiene le sue scelte in un JSON e dà le sue righe di
configurazione; qui si mettono insieme in ~/.config/aios/sistema-hyprland.conf, che aios-sessione copia accanto
alla configurazione di AIOS (source = sistema.conf). Mentre la sessione è aperta, i cambi si applicano subito con
«hyprctl keyword» (lo fa ogni parte) e si aggiorna anche la copia di lavoro, così un ricaricamento non li perde.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

Run = Callable[[list[str]], tuple[int, str]]


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios"


def conf_path() -> Path:
    return config_dir() / "sistema-hyprland.conf"


def run_copy() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "aios-hyprland" / "sistema.conf"


def hyprctl(args: list[str]) -> tuple[int, str]:
    if not shutil.which("hyprctl") or not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return 127, "Hyprland non è in esecuzione"
    try:
        p = subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=5)
        out = (p.stdout + p.stderr).strip()
        bad = out.lower().startswith(("invalid", "error", "unknown", "couldn't", "config error")) or "error" in out.lower()[:30]
        return (0 if p.returncode == 0 and not bad else 1), out
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def load(name: str, defaults: dict[str, Any]) -> dict[str, Any]:
    conf = json.loads(json.dumps(defaults))
    try:
        data = json.loads((config_dir() / f"{name}.json").read_text())
        if isinstance(data, dict):
            conf.update({k: data[k] for k in data if k in defaults})
    except (OSError, ValueError):
        pass
    return conf


def store(name: str, conf: dict[str, Any]) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / f"{name}.json").write_text(json.dumps(conf, indent=1, ensure_ascii=False))
    write()


def write() -> str:
    """Rigenera il file per il prossimo avvio (e la copia di lavoro della sessione aperta)."""
    parts = []
    for mod in ("monitor", "dispositivi", "accessibilita"):
        try:
            module = __import__(f"{__package__}.{mod}", fromlist=["hypr_lines"])
            lines = module.hypr_lines()
        except Exception:
            continue
        if lines:
            parts.append(f"# {mod}\n" + "\n".join(lines))
    text = "# Scritto da AIOS (Impostazioni): non modificare a mano, usa ~/.config/aios/hyprland.conf\n" + "\n\n".join(parts) + "\n"
    config_dir().mkdir(parents=True, exist_ok=True)
    conf_path().write_text(text)
    if run_copy().parent.is_dir():
        try:
            run_copy().write_text(text)
        except OSError:
            pass
    return text
