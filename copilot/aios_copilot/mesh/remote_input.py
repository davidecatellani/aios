"""Il telefono come tastiera e touchpad del PC.

Il PC riceve testo, tasti speciali e movimenti dalla pagina «Il mio PC» (solo da un
telefono abbinato e vicino) e li «digita» con lo strumento disponibile:
ydotool (qualsiasi desktop, Wayland o X11), wtype (Wayland wlroots) o xdotool (X11).
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Runner

MAX_TEXT = 2000
# tasto → (codice Linux per ydotool, nome per wtype/xdotool)
KEYS = {
    "invio": (28, "Return"), "cancella": (14, "BackSpace"), "canc": (111, "Delete"), "tab": (15, "Tab"),
    "esc": (1, "Escape"), "su": (103, "Up"), "giu": (108, "Down"), "sinistra": (105, "Left"), "destra": (106, "Right"),
    "inizio": (102, "Home"), "fine": (107, "End"), "spazio": (57, "space"), "pagsu": (104, "Prior"), "paggiu": (109, "Next"),
}


class InputInjector:
    def __init__(self, runner: Runner | None = None):
        self.runner = runner or Runner()
        self.backend = next((b for b in ("ydotool", "wtype", "xdotool") if self.runner.has(b)), "")

    def available(self) -> bool:
        return bool(self.backend)

    def _run(self, cmd: list[str]) -> bool:
        return self.runner.run(cmd)[0] == 0

    def text(self, text: str) -> bool:
        text = text[:MAX_TEXT]
        if not text:
            return True
        if self.backend == "ydotool":
            return self._run(["ydotool", "type", "--", text])
        if self.backend == "wtype":
            return self._run(["wtype", "--", text])
        return self._run(["xdotool", "type", "--clearmodifiers", "--", text])

    def key(self, name: str) -> bool:
        if name not in KEYS:
            return False
        code, sym = KEYS[name]
        if self.backend == "ydotool":
            return self._run(["ydotool", "key", f"{code}:1", f"{code}:0"])
        if self.backend == "wtype":
            return self._run(["wtype", "-k", sym])
        return self._run(["xdotool", "key", "--clearmodifiers", sym])

    def move(self, dx: int, dy: int) -> bool:
        dx, dy = max(-400, min(400, int(dx))), max(-400, min(400, int(dy)))
        if self.backend == "ydotool":
            return self._run(["ydotool", "mousemove", "-x", str(dx), "-y", str(dy)])
        if self.backend == "xdotool":
            return self._run(["xdotool", "mousemove_relative", "--", str(dx), str(dy)])
        return False  # wtype non muove il mouse

    def click(self, button: str = "sinistro") -> bool:
        if self.backend == "ydotool":
            return self._run(["ydotool", "click", "0xC1" if button == "destro" else "0xC0"])
        if self.backend == "xdotool":
            return self._run(["xdotool", "click", "3" if button == "destro" else "1"])
        return False

    def scroll(self, steps: int) -> bool:
        steps = max(-20, min(20, int(steps)))
        if not steps:
            return True
        if self.backend == "ydotool":
            return self._run(["ydotool", "mousemove", "--wheel", "-x", "0", "-y", str(-steps)])
        if self.backend == "xdotool":
            return self._run(["xdotool", "click", "--repeat", str(abs(steps)), "5" if steps > 0 else "4"])
        return False

    def handle(self, event: dict[str, Any]) -> bool:
        """Un evento dalla pagina del telefono."""
        kind = event.get("tipo")
        try:
            if kind == "testo":
                return self.text(str(event.get("testo", "")))
            if kind == "tasto":
                return self.key(str(event.get("tasto", "")))
            if kind == "muovi":
                return self.move(int(event.get("dx", 0)), int(event.get("dy", 0)))
            if kind == "click":
                return self.click(str(event.get("tasto", "sinistro")))
            if kind == "scorri":
                return self.scroll(int(event.get("passi", 0)))
        except (TypeError, ValueError):
            return False
        return False
