"""La tastiera di SoIA: la lingua scelta all'installazione, cambiabile da Impostazioni o a voce.

La scelta dell'utente sta in ~/.config/aios/tastiera. Con Hyprland (il compositore di SoIA) si applica
subito con «hyprctl keyword input:kb_layout»; aios-sessione la rilegge all'avvio. Con labwc (riserva)
si riscrive il suo ambiente ($XDG_RUNTIME_DIR/aios-labwc/environment) e «labwc --reconfigure».
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable

LAYOUTS = {
    "it": "Italiana", "us": "Inglese (USA)", "gb": "Inglese (Regno Unito)", "de": "Tedesca", "fr": "Francese",
    "es": "Spagnola", "ch": "Svizzera", "pt": "Portoghese", "br": "Brasiliana",
}
WORDS = {"italiana": "it", "italiano": "it", "inglese": "gb", "americana": "us", "usa": "us", "tedesca": "de",
         "tedesco": "de", "francese": "fr", "spagnola": "es", "spagnolo": "es", "svizzera": "ch", "portoghese": "pt",
         "brasiliana": "br"}


def choice_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "tastiera"


def session_env_file() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "aios-labwc" / "environment"


def current() -> str:
    try:
        value = choice_file().read_text().strip()
        if value in LAYOUTS:
            return value
    except OSError:
        pass
    value = os.environ.get("XKB_DEFAULT_LAYOUT", "it").split(",")[0]
    return value if value in LAYOUTS else "it"


def set_layout(layout: str, run: Callable[[list[str]], int] | None = None) -> str:
    layout = WORDS.get(layout.lower().strip(), layout.lower().strip())
    if layout not in LAYOUTS:
        return f"Non conosco la tastiera «{layout}». Posso mettere: " + ", ".join(LAYOUTS.values()) + "."
    f = choice_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(layout + "\n")
    runner = run or (lambda cmd: subprocess.run(cmd, capture_output=True).returncode)
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        try:
            runner(["hyprctl", "keyword", "input:kb_layout", layout])
        except OSError:
            pass
        return f"Tastiera {LAYOUTS[layout].lower()} attiva."
    env = session_env_file()
    if env.exists():  # sessione SoIA: si applica subito
        lines = [ln for ln in env.read_text().splitlines() if not ln.startswith("XKB_DEFAULT_LAYOUT=")]
        env.write_text("\n".join(lines + [f"XKB_DEFAULT_LAYOUT={layout}"]) + "\n")
        try:
            runner(["labwc", "--reconfigure"])
        except OSError:
            pass
    return f"Tastiera {LAYOUTS[layout].lower()} attiva."


RE_KEYBOARD = re.compile(r"^(?:metti|imposta|cambia|usa|passa\s+a)\s+(?:la\s+)?tastiera\s+(?:in\s+|a\s+|su\s+)?"
                         r"(?P<l>" + "|".join(WORDS) + r")$")
