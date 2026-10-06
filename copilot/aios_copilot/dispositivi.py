"""Mouse, touchpad e tastiera: velocità, accelerazione, mano sinistra, scorrimento (naturale e velocità), tocco per
fare clic, ripetizione dei tasti, Bloc Num all'avvio; e le scorciatoie, quelle di SoIA e quelle personali.

Una scorciatoia personale apre un programma oppure fa una richiesta a Nova («Super+M» → «metti la musica
rilassante»): così ogni combinazione di tasti può fare qualsiasi cosa che Nova sa fare.

Si applica subito con «hyprctl keyword input:…» e resta per il prossimo avvio (hyprconf.py).
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from . import hyprconf

DEFAULTS: dict[str, Any] = {
    "velocita": 0.0,  # -1 … 1
    "accelerazione": True,
    "mano_sinistra": False,
    "scorrimento_naturale_mouse": False,
    "velocita_scorrimento": 1.0,
    "tocco_clic": True,
    "scorrimento_naturale": True,  # touchpad
    "disattiva_scrivendo": True,
    "ripetizione_ritardo": 400,  # ms
    "ripetizione_velocita": 30,  # tasti al secondo
    "bloc_num": False,
    "scorciatoie": [],  # {"tasti": "SUPER+M", "app": "firefox"} oppure {"tasti": …, "chiedi": "…"}
}
# le scorciatoie di SoIA (hyprland.conf): per l'elenco nelle Impostazioni
BUILTIN = [
    ("Super", "La giornata (schermata principale)"), ("Super+Spazio", "Chiedi a Nova sopra qualsiasi programma"),
    ("Super+V", "Cronologia degli appunti"), ("Super+.", "Emoji"), ("Super+N", "Centro notifiche"),
    ("Alt+Tab", "Passa al programma successivo"), ("Super+← / →", "Programma precedente / successivo"),
    ("Super+Q · Alt+F4", "Chiudi il programma"), ("Super+L", "Blocca lo schermo"), ("Stamp", "Schermata"),
    ("Maiusc+Stamp", "Schermata di una zona"), ("Super e + / - / 0", "Zoom"), ("Super+Alt+S", "Lettore dello schermo"),
]
RESERVED = {"SUPER+SPACE", "SUPER+A", "SUPER+V", "SUPER+PERIOD", "SUPER+N", "SUPER+Q", "SUPER+L", "SUPER+LEFT", "SUPER+RIGHT",
            "ALT+TAB", "ALT+SHIFT+TAB", "ALT+F4", "SUPER+PLUS", "SUPER+MINUS", "SUPER+0", "SUPER+EQUAL", "SUPER+ALT+S"}
MODS = {"SUPER", "CTRL", "ALT", "SHIFT"}


def settings() -> dict[str, Any]:
    return hyprconf.load("dispositivi", DEFAULTS)


def normalize_keys(keys: str) -> str | None:
    """«super + m», «Ctrl+Alt+T» → «SUPER+M», «CTRL+ALT+T»; None se non è una combinazione valida."""
    parts = [p.strip().upper() for p in re.split(r"\s*\+\s*", keys.strip()) if p.strip()]
    parts = [{"WIN": "SUPER", "META": "SUPER", "CONTROL": "CTRL", "MAIUSC": "SHIFT", "CMD": "SUPER"}.get(p, p) for p in parts]
    mods = [p for p in parts if p in MODS]
    rest = [p for p in parts if p not in MODS]
    if not mods or len(rest) != 1 or not re.fullmatch(r"[A-Z0-9]|F\d{1,2}|[A-Z_]+", rest[0]):
        return None
    order = ["SUPER", "CTRL", "ALT", "SHIFT"]
    return "+".join(sorted(set(mods), key=order.index) + rest)


def bind_line(sc: dict[str, Any]) -> str | None:
    keys = normalize_keys(sc.get("tasti", ""))
    if not keys or keys in RESERVED:
        return None
    *mods, key = keys.split("+")
    if sc.get("app"):
        if not re.fullmatch(r"[\w.-]+", sc["app"]):
            return None
        action = f"gtk-launch {sc['app']}"
    elif sc.get("chiedi"):
        text = re.sub(r"[\n\r]", " ", str(sc["chiedi"]))[:300]
        action = "aios-shell --chiedi " + shlex.quote(text)
    else:
        return None
    return f"bind = {' '.join(mods)}, {key}, exec, {action}"


def hypr_lines() -> list[str]:
    s = settings()
    lines = ["input {",
             f"    sensitivity = {max(-1.0, min(1.0, float(s['velocita']))):.2f}",
             f"    accel_profile = {'adaptive' if s['accelerazione'] else 'flat'}",
             f"    left_handed = {str(bool(s['mano_sinistra'])).lower()}",
             f"    natural_scroll = {str(bool(s['scorrimento_naturale_mouse'])).lower()}",
             f"    scroll_factor = {float(s['velocita_scorrimento']):.2f}",
             f"    repeat_delay = {int(s['ripetizione_ritardo'])}",
             f"    repeat_rate = {int(s['ripetizione_velocita'])}",
             f"    numlock_by_default = {str(bool(s['bloc_num'])).lower()}",
             "    touchpad {",
             f"        natural_scroll = {str(bool(s['scorrimento_naturale'])).lower()}",
             f"        tap-to-click = {str(bool(s['tocco_clic'])).lower()}",
             f"        disable_while_typing = {str(bool(s['disattiva_scrivendo'])).lower()}",
             f"        scroll_factor = {float(s['velocita_scorrimento']):.2f}",
             "    }", "}"]
    lines += [b for b in (bind_line(sc) for sc in s["scorciatoie"]) if b]
    return lines


KEYWORDS = {"velocita": "input:sensitivity", "mano_sinistra": "input:left_handed", "scorrimento_naturale_mouse": "input:natural_scroll",
            "ripetizione_ritardo": "input:repeat_delay", "ripetizione_velocita": "input:repeat_rate",
            "bloc_num": "input:numlock_by_default", "tocco_clic": "input:touchpad:tap-to-click",
            "scorrimento_naturale": "input:touchpad:natural_scroll", "disattiva_scrivendo": "input:touchpad:disable_while_typing"}


def apply(changes: dict[str, Any], hypr: hyprconf.Run = hyprconf.hyprctl) -> tuple[dict[str, Any], str]:
    conf = settings()
    for k, v in changes.items():
        if k not in DEFAULTS or k == "scorciatoie":
            continue
        if isinstance(DEFAULTS[k], bool):
            v = bool(v)
        elif k == "velocita":
            v = max(-1.0, min(1.0, float(v)))
        elif k == "velocita_scorrimento":
            v = max(0.2, min(3.0, float(v)))
        elif k == "ripetizione_ritardo":
            v = int(max(150, min(1500, int(v))))
        elif k == "ripetizione_velocita":
            v = int(max(5, min(80, int(v))))
        conf[k] = v
        value = ("1" if v else "0") if isinstance(v, bool) else (f"{v:.2f}" if isinstance(v, float) else str(v))
        if k == "accelerazione":
            hypr(["keyword", "input:accel_profile", "adaptive" if v else "flat"])
        elif k == "velocita_scorrimento":
            hypr(["keyword", "input:scroll_factor", value])
            hypr(["keyword", "input:touchpad:scroll_factor", value])
        elif k in KEYWORDS:
            hypr(["keyword", KEYWORDS[k], value])
    hyprconf.store("dispositivi", conf)
    return conf, "Fatto."


def add_shortcut(keys: str, app: str = "", ask: str = "", hypr: hyprconf.Run = hyprconf.hyprctl) -> tuple[bool, str]:
    norm = normalize_keys(keys)
    if norm is None:
        return False, "Serve un tasto con Super, Ctrl o Alt (es. Super+M)."
    if norm in RESERVED:
        return False, f"{pretty(norm)} la usa già SoIA."
    sc = {"tasti": norm, "app": app.strip()} if app.strip() else {"tasti": norm, "chiedi": ask.strip()}
    line = bind_line(sc)
    if line is None:
        return False, "Dimmi cosa deve fare: un programma o una richiesta a Nova."
    conf = settings()
    old = next((x for x in conf["scorciatoie"] if x["tasti"] == norm), None)
    if old:
        remove_shortcut(norm, hypr)
        conf = settings()
    conf["scorciatoie"].append(sc)
    hyprconf.store("dispositivi", conf)
    *mods, key = norm.split("+")
    hypr(["keyword", "bind", line.split("=", 1)[1].strip()])
    what = f"apre {app}" if app.strip() else f"chiede a Nova «{ask.strip()}»"
    return True, f"{pretty(norm)} {what}."


def remove_shortcut(keys: str, hypr: hyprconf.Run = hyprconf.hyprctl) -> bool:
    norm = normalize_keys(keys) or keys
    conf = settings()
    rest = [x for x in conf["scorciatoie"] if x["tasti"] != norm]
    if len(rest) == len(conf["scorciatoie"]):
        return False
    conf["scorciatoie"] = rest
    hyprconf.store("dispositivi", conf)
    *mods, key = norm.split("+")
    hypr(["keyword", "unbind", f"{' '.join(mods)}, {key}"])
    return True


def pretty(keys: str) -> str:
    names = {"SUPER": "Super", "CTRL": "Ctrl", "ALT": "Alt", "SHIFT": "Maiusc", "PERIOD": ".", "SPACE": "Spazio"}
    return "+".join(names.get(p, p.capitalize() if len(p) > 1 else p) for p in keys.split("+"))
