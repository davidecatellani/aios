"""Accessibilità: lettore dello schermo, zoom, contrasto alto, puntatore grande, meno animazioni, filtri per chi
vede i colori in modo diverso, sottotitoli in tempo reale di tutto quello che il PC fa sentire.

- Lettore dello schermo: Orca (legge le app e le pagine di AIOS); Super+Alt+S lo accende e lo spegne.
- Zoom: Super++ e Super+- ingrandiscono attorno al puntatore (Hyprland, cursor:zoom_factor); Super+0 torna normale.
- Contrasto alto: le pagine di AIOS con colori pieni e bordi netti, le app GTK col tema ad alto contrasto.
- Filtri colore: shader di Hyprland per deuteranopia, protanopia, tritanopia, scala di grigi, colori invertiti.
- Sottotitoli: l'audio del PC (video, chiamate, giochi) trascritto da Parakeet in una striscia in basso.

Le scelte stanno in ~/.config/aios/accessibilita.json; quelle di Hyprland anche nel sistema.conf (hyprconf.py).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import hyprconf

DEFAULTS = {"lettore": False, "contrasto": False, "cursore_grande": False, "meno_animazioni": False, "filtro": "",
            "sottotitoli": False}
FILTERS = {"": "Nessuno", "deuteranopia": "Verde-rosso (deuteranopia)", "protanopia": "Rosso-verde (protanopia)",
           "tritanopia": "Blu-giallo (tritanopia)", "grigi": "Scala di grigi", "inverti": "Colori invertiti"}
SHADERS = Path("/usr/share/aios/hyprland/filtri")
CURSOR = ("Adwaita", 24, 48)

Run = Callable[[list[str]], tuple[int, str]]


def settings() -> dict[str, Any]:
    return hyprconf.load("accessibilita", DEFAULTS)


def hypr_lines() -> list[str]:
    s = settings()
    lines = []
    if s["meno_animazioni"]:
        lines += ["animations {", "    enabled = false", "}"]
    if s["cursore_grande"]:
        lines += [f"env = XCURSOR_SIZE,{CURSOR[2]}", f"env = HYPRCURSOR_SIZE,{CURSOR[2]}"]
    if s["filtro"] in FILTERS and s["filtro"]:
        lines += ["decoration {", f"    screen_shader = {SHADERS / (s['filtro'] + '.frag')}", "}"]
    return lines


def _run(cmd: list[str]) -> tuple[int, str]:
    if not shutil.which(cmd[0]):
        return 127, ""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return p.returncode, p.stdout + p.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def screen_reader(on: bool, run: Run = _run, spawn: Callable[[list[str]], Any] | None = None) -> bool:
    spawn = spawn or (lambda cmd: subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    run(["gsettings", "set", "org.gnome.desktop.a11y.applications", "screen-reader-enabled", "true" if on else "false"])
    if not on:
        run(["pkill", "-x", "orca"])
        return True
    if not shutil.which("orca"):
        return False
    spawn(["orca", "--replace"])
    return True


def apply(changes: dict[str, Any], run: Run = _run, hypr: Run = hyprconf.hyprctl,
          captions: Callable[[bool], None] = lambda on: None) -> tuple[dict[str, Any], str]:
    """Salva e applica subito; → (scelte, messaggio)."""
    conf = settings()
    notes = []
    for k, v in changes.items():
        if k not in DEFAULTS:
            continue
        if k == "filtro":
            v = v if v in FILTERS else ""
        else:
            v = bool(v)
        conf[k] = v
        if k == "lettore":
            if not screen_reader(v, run):
                notes.append("Il lettore dello schermo (Orca) non è installato.")
        elif k == "contrasto":
            run(["gsettings", "set", "org.gnome.desktop.a11y.interface", "high-contrast", "true" if v else "false"])
        elif k == "cursore_grande":
            size = CURSOR[2] if v else CURSOR[1]
            hypr(["setcursor", CURSOR[0], str(size)])
            run(["gsettings", "set", "org.gnome.desktop.interface", "cursor-size", str(size)])
        elif k == "meno_animazioni":
            hypr(["keyword", "animations:enabled", "0" if v else "1"])
            run(["gsettings", "set", "org.gnome.desktop.interface", "enable-animations", "false" if v else "true"])
        elif k == "filtro":
            hypr(["keyword", "decoration:screen_shader", str(SHADERS / f"{v}.frag") if v else "[[EMPTY]]"])
        elif k == "sottotitoli":
            captions(v)
    hyprconf.store("accessibilita", conf)
    return conf, " ".join(notes) or "Fatto."


def zoom(step: str, hypr: Run = hyprconf.hyprctl) -> float:
    """«+», «-» o «0»: lo zoom attorno al puntatore."""
    code, out = hypr(["getoption", "cursor:zoom_factor"])
    try:
        current = float(next(l.split(":", 1)[1] for l in out.splitlines() if l.strip().startswith("float")))
    except (StopIteration, ValueError):
        current = 1.0
    new = 1.0 if step == "0" else max(1.0, min(6.0, current * (1.25 if step == "+" else 0.8)))
    hypr(["keyword", "cursor:zoom_factor", f"{new:.2f}"])
    return new


def startup(captions: Callable[[bool], None] = lambda on: None) -> None:
    """All'avvio della sessione: il lettore e i sottotitoli, se erano accesi."""
    s = settings()
    if s["lettore"]:
        screen_reader(True)
    if s["sottotitoli"]:
        captions(True)
