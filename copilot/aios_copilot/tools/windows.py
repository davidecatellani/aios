"""Programmi a finestra e app di AIOS, comandati da Nova.

Nella sessione AIOS le cose di base (file, foto, musica, video, note, impostazioni) sono app HTML
della shell; i programmi installati (Firefox, giochi, app Windows…) sono finestre a schermo intero
che la shell e Nova tengono in ordine: «passa a Firefox», «chiudi Spotify», «quali programmi sono
aperti?», «torna alla schermata», «apri le impostazioni del Wi-Fi».
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Callable

from ..fastpath import Intent, normalize
from .base import Tool, params

VIEWS = {"foto": "foto", "fotografie": "foto", "immagini": "foto", "musica": "musica", "canzoni": "musica",
         "brani": "musica", "video": "video", "filmati": "video", "note": "note", "appunti": "note", "file": "file",
         "documenti": "file", "cartelle": "file", "impostazioni": "impostazioni", "preferenze": "impostazioni"}
SECTIONS = {"wifi": "wifi", "wi-fi": "wifi", "rete": "wifi", "internet": "wifi", "bluetooth": "bluetooth",
            "volume": "suono", "suono": "suono", "audio": "suono", "luminosita": "suono", "luminosità": "suono",
            "schermo": "suono", "voce": "voce", "password": "account", "aggiornamenti": "aggiornamenti",
            "computer": "info", "sistema": "info"}


def in_aios_session() -> bool:
    return "AIOS" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()


def _shell(*args: str) -> bool:
    exe = shutil.which("aios-shell")
    if not exe:
        return False
    try:
        subprocess.Popen([exe, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def match_window(name: str, windows: list[dict[str, str]]) -> dict[str, str] | None:
    """La finestra che corrisponde al nome detto: nell'app_id (org.mozilla.firefox) o nel titolo."""
    key = re.sub(r"[^a-z0-9]", "", normalize(name).lower())
    if len(key) < 2:
        return None
    for w in windows:
        app_id = re.sub(r"[^a-z0-9]", "", w["app_id"].lower().rsplit(".", 1)[-1])
        if key == app_id or key in app_id:
            return w
    for w in windows:
        if key in re.sub(r"[^a-z0-9]", "", w["title"].lower()):
            return w
    return None


def make_tools(windows: Callable[[], list[dict[str, str]]] | None = None,
               focus: Callable[[str], bool] | None = None, close: Callable[[str], bool] | None = None,
               shell: Callable[..., bool] = _shell) -> list[Tool]:
    from .. import shell as sh

    windows = windows or sh.open_windows
    focus = focus or sh.focus_window
    close = close or sh.close_window

    def list_windows() -> str:
        ws = windows()
        if not ws:
            return "Non c'è nessun programma aperto."
        return "Programmi aperti: " + ", ".join(w["title"] or w["app_id"] for w in ws) + "."

    def switch_window(name: str) -> str:
        w = match_window(name, windows())
        if w is None:
            return f"«{name}» non è aperto. Vuoi che lo apra? Dimmi «apri {name}»."
        return f"Ecco {w['title'] or w['app_id']}." if focus(w["app_id"]) else "Non riesco a portarlo davanti."

    def close_window(name: str) -> str:
        w = match_window(name, windows())
        if w is None:
            return f"«{name}» non è aperto."
        return f"Chiuso {w['title'] or w['app_id']}." if close(w["app_id"]) else "Non riesco a chiuderlo."

    def close_all() -> str:
        ws = windows()
        done = [w for w in ws if close(w["app_id"])]
        return f"Chiusi {len(done)} programmi." if done else "Non c'era niente da chiudere."

    def go_home() -> str:
        return "Ecco la schermata." if shell("--casa") else "La shell di AIOS non è in esecuzione."

    def show_view(vista: str, sezione: str = "") -> str:
        target = VIEWS.get(vista.lower(), vista.lower())
        if target not in set(VIEWS.values()):
            return f"Non conosco l'app «{vista}»."
        arg = target + (f":{SECTIONS.get(sezione.lower(), sezione.lower())}" if sezione and target == "impostazioni" else "")
        return f"Apro {vista}." if shell("--vista", arg) else "La shell di AIOS non è in esecuzione."

    def keyboard_layout(lingua: str) -> str:
        from .. import keyboard

        return keyboard.set_layout(lingua)

    return [
        Tool("set_keyboard", "Cambia la lingua della tastiera (italiana, inglese, americana, tedesca, francese, spagnola…).",
             params(lingua="Lingua della tastiera"), keyboard_layout),
        Tool("list_windows", "Dice quali programmi a finestra sono aperti.", params(), list_windows),
        Tool("switch_window", "Porta davanti un programma già aperto.", params(name="Nome del programma (es. Firefox)"),
             switch_window),
        Tool("close_window", "Chiude un programma aperto.", params(name="Nome del programma da chiudere"), close_window),
        Tool("close_all_windows", "Chiude tutti i programmi aperti.", params(), close_all, requires_confirmation=True),
        Tool("go_home", "Torna alla schermata principale di AIOS (riduce i programmi aperti).", params(), go_home),
        Tool("show_aios_app", "Apre un'app di AIOS: file, foto, musica, video, note o impostazioni (anche una sezione: "
             "wifi, bluetooth, suono, voce, password, aggiornamenti).",
             params(vista=("App", ["file", "foto", "musica", "video", "note", "impostazioni"]),
                    sezione="Sezione delle impostazioni (facoltativa)"), show_view),
    ]


RE_CLOSE_ALL = re.compile(r"^chiudi\s+(?:tutto|tutti\s+i\s+programmi|tutte\s+le\s+(?:finestre|app))$")
RE_CLOSE = re.compile(r"^(?:chiudi|chiudere|termina|esci\s+da)\s+(?:il\s+|la\s+|lo\s+|l')?(?P<n>[\w .'-]{2,40})$")
RE_SWITCH = re.compile(r"^(?:passa\s+a|torna\s+(?:su|a)|vai\s+su|metti\s+davanti)\s+(?:il\s+|la\s+|lo\s+|l')?(?P<n>[\w .'-]{2,40})$")
RE_HOME = re.compile(r"^(?:torna\s+(?:alla\s+schermata|a\s+casa|alla\s+home)|vai\s+alla\s+(?:schermata|home)|riduci\s+tutto"
                     r"|mostra(?:mi)?\s+la\s+schermata)$")
RE_LIST = re.compile(r"^(?:che|quali)\s+(?:programmi|app|finestre)\s+(?:ho\s+|sono\s+)?(?:aperti|aperte)\??$")
RE_VIEW = re.compile(r"^(?:apri|aprimi|mostra|mostrami|fammi\s+vedere|vai\s+(?:su|a|alle|alla|ai|nelle|nella))\s+"
                     r"(?:le\s+|la\s+|i\s+|il\s+|gli\s+|l')?(?:mie\s+|miei\s+)?(?P<v>" + "|".join(VIEWS) + r")"
                     r"(?:\s+(?:del|della|dello|dei|di)\s+(?:l')?(?P<s>" + "|".join(re.escape(k) for k in SECTIONS) + r"))?$")


class WindowsRouter:
    """Frasi sui programmi aperti e sulle app di AIOS, senza modello AI (solo nella sessione AIOS)."""

    def __init__(self, windows: Callable[[], list[dict[str, str]]] | None = None, active: Callable[[], bool] = in_aios_session):
        self.windows, self.active = windows, active

    def _windows(self) -> list[dict[str, str]]:
        if self.windows is not None:
            return self.windows()
        from .. import shell as sh

        return sh.open_windows()

    def match(self, text: str) -> Intent | None:
        if not self.active():
            return None
        low = normalize(text).lower().strip(" .!?")
        from ..keyboard import RE_KEYBOARD

        m = RE_KEYBOARD.match(low)
        if m:
            return Intent("set_keyboard", {"lingua": m.group("l")})
        if RE_LIST.match(low):
            return Intent("list_windows", {})
        if RE_HOME.match(low):
            return Intent("go_home", {})
        if RE_CLOSE_ALL.match(low):
            return Intent("close_all_windows", {})
        m = RE_VIEW.match(low)
        if m:
            return Intent("show_aios_app", {"vista": m.group("v"), "sezione": m.group("s") or ""})
        for rx, tool in ((RE_CLOSE, "close_window"), (RE_SWITCH, "switch_window")):
            m = rx.match(low)
            if m and match_window(m.group("n"), self._windows()) is not None:
                return Intent(tool, {"name": m.group("n")})
        return None
