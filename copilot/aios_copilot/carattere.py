"""Carattere e dimensione del testo per tutto SoIA: «ingrandisci il testo», «usa un carattere più
leggibile», «metti il font Lexend», Impostazioni › Aspetto.

La scelta sta in ~/.config/aios/aspetto.json. Le pagine di SoIA (schermata, barra, app, pannello di
Nova) la leggono da /api/aspetto e la applicano subito; i programmi GTK la ricevono da gsettings
(carattere dell'interfaccia e fattore di scala del testo) e la usano alla prossima apertura, o
subito se la seguono già (Firefox, le app GNOME).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

# I caratteri proposti: nome per Nova → famiglia, con una riga che spiega a cosa serve.
CHOICES: dict[str, tuple[str, str]] = {
    "normale": ("Inter", "Quello di SoIA: moderno e pulito"),
    "leggibile": ("Atkinson Hyperlegible", "Disegnato per chi vede poco: lettere che non si confondono"),
    "dislessia": ("OpenDyslexic", "Lettere col fondo pesante, pensato per la dislessia"),
    "lettura": ("Lexend", "Spaziato e morbido, si legge con meno fatica"),
    "classico": ("Noto Serif", "Con le grazie, come un libro"),
    "fira": ("Fira Sans", "Umanista, caldo"),
    "macchina": ("JetBrains Mono", "Monospaziato, come una macchina da scrivere"),
}
DEFAULT = {"carattere": "Inter", "scala": 1.0}
MIN_SCALE, MAX_SCALE, STEP = 0.8, 1.6, 0.1
ALIASES = {"piu leggibile": "leggibile", "leggibilita": "leggibile", "ipovedenti": "leggibile", "dislessici": "dislessia",
           "dislessico": "dislessia", "con le grazie": "classico", "serif": "classico", "libro": "classico",
           "monospaziato": "macchina", "macchina da scrivere": "macchina", "predefinito": "normale",
           "di aios": "normale", "originale": "normale", "standard": "normale"}


def path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "aspetto.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(path().read_text())
        if isinstance(data, dict):
            return {**DEFAULT, **{k: data[k] for k in DEFAULT if k in data}}
    except (OSError, ValueError):
        pass
    return dict(DEFAULT)


def save(data: dict[str, Any]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False))


def installed_families(run: Callable[[list[str]], str] | None = None) -> set[str]:
    def _fc(cmd: list[str]) -> str:
        if not shutil.which(cmd[0]):
            return ""
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    out = (run or _fc)(["fc-list", ":", "family"])
    return {f.strip() for line in out.splitlines() for f in line.split(",") if f.strip()}


def offered(families: set[str] | None = None) -> list[dict[str, str]]:
    """I caratteri da mostrare in Impostazioni: quelli proposti che ci sono (tutti, se non si sa)."""
    families = installed_families() if families is None else families
    found = [{"nome": key, "famiglia": fam, "descrizione": desc} for key, (fam, desc) in CHOICES.items()
             if not families or fam in families]
    return found or [{"nome": key, "famiglia": fam, "descrizione": desc} for key, (fam, desc) in CHOICES.items()]


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ù", "u").replace("à", "a").replace("è", "e").strip(" .!?«»\"'"))


def resolve_family(name: str, families: set[str] | None = None) -> str | None:
    key = _plain(name)
    key = re.sub(r"^(?:il |un |carattere |font |più |piu )+", "", key)
    key = ALIASES.get(key, key)
    if key in CHOICES:
        return CHOICES[key][0]
    families = installed_families() if families is None else families
    for fam in sorted(families | {f for f, _ in CHOICES.values()}):
        if _plain(fam) == key:
            return fam
    for fam in sorted(families):
        if key and key in _plain(fam):
            return fam
    return None


def parse_scale(text: str, current: float) -> float | None:
    t = _plain(text)
    m = re.search(r"(\d{2,3})\s*%", t)
    if m:
        value = int(m.group(1)) / 100
    elif re.search(r"\b(?:normale|predefinit[ao]|standard|originale|come prima)\b", t):
        value = 1.0
    elif re.search(r"\b(?:molto|tanto|parecchio)\b.*\b(?:grand|ingrand)", t):
        value = current + 2 * STEP
    elif re.search(r"\b(?:molto|tanto|parecchio)\b.*\b(?:piccol|rimpiccol)", t):
        value = current - 2 * STEP
    elif re.search(r"grand|ingrand|aument", t):
        value = current + STEP
    elif re.search(r"piccol|rimpiccol|riduci|diminu", t):
        value = current - STEP
    else:
        try:
            value = float(t.replace(",", "."))
        except ValueError:
            return None
    return round(max(MIN_SCALE, min(MAX_SCALE, value)), 2)


def apply_gtk(data: dict[str, Any], run: Callable[[list[str]], Any] | None = None) -> None:
    """Gli stessi valori ai programmi GTK (Firefox, LibreOffice, app GNOME)."""
    def _run(cmd: list[str]) -> None:
        if shutil.which(cmd[0]):
            subprocess.run(cmd, capture_output=True, timeout=10)

    run = run or _run
    try:
        run(["gsettings", "set", "org.gnome.desktop.interface", "font-name", f"{data['carattere']} 11"])
        run(["gsettings", "set", "org.gnome.desktop.interface", "text-scaling-factor", f"{float(data['scala']):.2f}"])
    except (OSError, subprocess.SubprocessError):
        pass


def set_appearance(carattere: str = "", dimensione: str = "", families: set[str] | None = None,
                   gtk: Callable[[dict[str, Any]], None] = apply_gtk) -> str:
    data = load()
    said = []
    if carattere.strip():
        fam = resolve_family(carattere, families)
        if fam is None:
            names = ", ".join(c["nome"] for c in offered(families))
            return f"Non conosco il carattere «{carattere}». Posso usare: {names}."
        data["carattere"] = fam
        said.append(f"carattere {fam}")
    if dimensione.strip():
        scale = parse_scale(dimensione, float(data["scala"]))
        if scale is None:
            return f"Non capisco la dimensione «{dimensione}». Prova con «più grande», «più piccolo» o «120%»."
        if scale == data["scala"] and scale in (MIN_SCALE, MAX_SCALE):
            return "Il testo è già " + ("al massimo." if scale == MAX_SCALE else "al minimo.")
        data["scala"] = scale
        said.append(f"testo al {round(scale * 100)}%")
    if not said:
        return f"Ora uso {data['carattere']} con il testo al {round(float(data['scala']) * 100)}%."
    save(data)
    gtk(data)
    return "Fatto: " + " e ".join(said) + ". I programmi già aperti lo prendono quando li riapri."
