"""Gli schermi: risoluzione, frequenza (60, 144 Hz…), scala, rotazione, più monitor (accanto, sopra, duplicato),
spegnere uno schermo, sincronizzazione adattiva (VRR/FreeSync/G-Sync).

Legge «hyprctl monitors all -j»; applica con «hyprctl keyword monitor …» e ricorda la scelta (hyprconf.py) per
il prossimo avvio. Dalle Impostazioni ogni cambio va confermato entro 15 secondi, come su Windows: se lo schermo
diventa nero e non si risponde, si torna da soli alla scelta di prima.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Callable

from . import hyprconf

Run = Callable[[list[str]], tuple[int, str]]
SCALES = [1.0, 1.25, 1.5, 1.75, 2.0]
ROTATIONS = {0: "normale", 1: "90° (verticale)", 2: "capovolto", 3: "270° (verticale)"}
CONFIRM_SECONDS = 15


def _mode(text: str) -> tuple[int, int, float] | None:
    m = re.match(r"(\d+)x(\d+)@([\d.]+)", text)
    return (int(m.group(1)), int(m.group(2)), float(m.group(3))) if m else None


def parse(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    out = []
    for m in data if isinstance(data, list) else []:
        modes = [x for x in (_mode(s) for s in m.get("availableModes") or []) if x]
        res: dict[str, list[float]] = {}
        for w, h, r in sorted(modes, key=lambda x: (-x[0] * x[1], -x[2])):
            rates = res.setdefault(f"{w}x{h}", [])
            if not any(abs(r - x) < 0.5 for x in rates):
                rates.append(round(r, 2))
        name = " ".join(x for x in (m.get("make"), m.get("model")) if x and x != "Unknown") or m.get("description") or m["name"]
        out.append({
            "nome": m["name"], "descrizione": name.strip(), "larghezza": m.get("width", 0), "altezza": m.get("height", 0),
            "frequenza": round(float(m.get("refreshRate", 60)), 2), "x": m.get("x", 0), "y": m.get("y", 0),
            "scala": float(m.get("scale", 1)), "rotazione": int(m.get("transform", 0)) % 4, "spento": bool(m.get("disabled")),
            "vrr": bool(m.get("vrr")), "risoluzioni": res, "duplica": m.get("mirrorOf", "none") not in ("none", "", None),
            "interno": m["name"].startswith(("eDP", "LVDS", "DSI")),
        })
    return out


def rule(m: dict[str, Any]) -> str:
    """La regola «monitor = …» di Hyprland per uno schermo."""
    if m.get("spento"):
        return f"{m['nome']}, disable"
    mode = f"{m['larghezza']}x{m['altezza']}@{m['frequenza']:g}" if m.get("larghezza") else "preferred"
    pos = f"{m['x']}x{m['y']}" if m.get("x") is not None and m.get("y") is not None else "auto"
    text = f"{m['nome']}, {mode}, {pos}, {m.get('scala', 1):g}"
    if m.get("rotazione"):
        text += f", transform, {m['rotazione']}"
    if m.get("duplica_di"):
        text += f", mirror, {m['duplica_di']}"
    if m.get("vrr"):
        text += ", vrr, 1"
    return text


def settings() -> dict[str, Any]:
    return hyprconf.load("monitor", {"schermi": {}})


def hypr_lines() -> list[str]:
    return [f"monitor = {rule(m)}" for m in settings()["schermi"].values()]


def place(moved: dict[str, Any], other: dict[str, Any], where: str) -> tuple[int, int]:
    """La posizione di uno schermo accanto a un altro (in pixel logici: divisi per la scala)."""
    def size(m: dict[str, Any]) -> tuple[int, int]:
        w, h = m["larghezza"], m["altezza"]
        if m.get("rotazione", 0) % 2:
            w, h = h, w
        return int(w / m.get("scala", 1)), int(h / m.get("scala", 1))

    ow, oh = size(other)
    mw, mh = size(moved)
    x, y = other["x"], other["y"]
    return {"destra": (x + ow, y), "sinistra": (x - mw, y), "sopra": (x, y - mh), "sotto": (x, y + oh)}.get(where, (x + ow, y))


class Monitors:
    def __init__(self, run: Run = hyprconf.hyprctl, timer: Callable[[float, Callable[[], None]], Any] | None = None):
        self.run = run
        self.timer = timer or (lambda secs, fn: threading.Timer(secs, fn))
        self.pending: Any = None
        self.previous: dict[str, Any] | None = None

    def state(self) -> list[dict[str, Any]]:
        code, out = self.run(["monitors", "all", "-j"])
        return parse(out) if code == 0 else []

    def _apply_rule(self, m: dict[str, Any]) -> tuple[bool, str]:
        code, out = self.run(["keyword", "monitor", rule(m)])
        return code == 0, out

    def change(self, name: str, changes: dict[str, Any], confirm: bool = True) -> tuple[bool, str]:
        screens = {m["nome"]: m for m in self.state()}
        if name not in screens:
            name = next((n for n, m in screens.items() if name.lower() in (n + " " + m["descrizione"]).lower()), "") \
                or (next(iter(screens)) if len(screens) == 1 or not name else "")
        if name not in screens:
            return False, "Non trovo quello schermo."
        m = dict(screens[name])
        before = dict(m)
        if "risoluzione" in changes:
            w, h = (int(x) for x in str(changes["risoluzione"]).lower().split("x"))
            if f"{w}x{h}" not in m["risoluzioni"]:
                return False, f"Lo schermo non offre {w}×{h}. Può: " + ", ".join(list(m["risoluzioni"])[:6]) + "."
            m["larghezza"], m["altezza"] = w, h
            if not any(abs(m["frequenza"] - r) < 0.5 for r in m["risoluzioni"][f"{w}x{h}"]):
                m["frequenza"] = m["risoluzioni"][f"{w}x{h}"][0]
        if "frequenza" in changes:
            rates = m["risoluzioni"].get(f"{m['larghezza']}x{m['altezza']}", [])
            want = float(changes["frequenza"])
            best = min(rates, key=lambda r: abs(r - want), default=None)
            if best is None or abs(best - want) > 1.5:
                return False, f"A {m['larghezza']}×{m['altezza']} lo schermo arriva a: " + ", ".join(f"{r:g} Hz" for r in rates) + "."
            m["frequenza"] = best
        if "scala" in changes:
            m["scala"] = max(0.5, min(3.0, float(changes["scala"])))
        if "rotazione" in changes:
            m["rotazione"] = int(changes["rotazione"]) % 4
        if "vrr" in changes:
            m["vrr"] = bool(changes["vrr"])
        if "spento" in changes:
            if changes["spento"] and sum(1 for s in screens.values() if not s["spento"]) <= 1:
                return False, "È l'unico schermo acceso: non lo spengo."
            m["spento"] = bool(changes["spento"])
        if changes.get("accanto") and changes.get("di") in screens:
            m["x"], m["y"] = place(m, screens[changes["di"]], str(changes["accanto"]))
        m["duplica_di"] = changes.get("duplica_di") if changes.get("duplica_di") in screens else None
        ok, out = self._apply_rule(m)
        if not ok:
            return False, f"Hyprland non ha accettato la modifica: {out[:120]}"
        self._remember(m)
        if confirm:
            self._arm(before)
        return True, describe(m)

    def _remember(self, m: dict[str, Any]) -> None:
        conf = settings()
        keep = {k: m.get(k) for k in ("nome", "larghezza", "altezza", "frequenza", "x", "y", "scala", "rotazione", "vrr", "spento", "duplica_di")}
        conf["schermi"][m["nome"]] = keep
        hyprconf.store("monitor", conf)

    def _arm(self, before: dict[str, Any]) -> None:
        self.cancel_timer()
        self.previous = before
        self.pending = self.timer(CONFIRM_SECONDS, self.revert)
        self.pending.daemon = True
        self.pending.start()

    def cancel_timer(self) -> None:
        if self.pending is not None:
            self.pending.cancel()
        self.pending = None

    def confirm(self) -> bool:
        had = self.pending is not None
        self.cancel_timer()
        self.previous = None
        return had

    def revert(self) -> bool:
        self.cancel_timer()
        before, self.previous = self.previous, None
        if before is None:
            return False
        self._apply_rule(before)
        self._remember(before)
        return True

    def reset(self) -> None:
        """Torna alle scelte automatiche (risoluzione e frequenza migliori, uno accanto all'altro)."""
        hyprconf.store("monitor", {"schermi": {}})
        for m in self.state():
            self.run(["keyword", "monitor", f"{m['nome']}, preferred, auto, 1"])


def describe(m: dict[str, Any]) -> str:
    if m.get("spento"):
        return f"Schermo {m['descrizione'] if 'descrizione' in m else m['nome']} spento."
    bits = [f"{m['larghezza']}×{m['altezza']}", f"{m['frequenza']:g} Hz", f"scala {round(m['scala'] * 100)}%"]
    if m.get("rotazione"):
        bits.append("ruotato " + ROTATIONS[m["rotazione"]])
    if m.get("duplica_di"):
        bits.append(f"duplica {m['duplica_di']}")
    return f"{m.get('descrizione') or m['nome']}: " + ", ".join(bits) + "."


def best_rate_advice(screens: list[dict[str, Any]]) -> list[str]:
    """Uno schermo che potrebbe andare più veloce di come va (il classico 144 Hz lasciato a 60)."""
    out = []
    for m in screens:
        rates = m["risoluzioni"].get(f"{m['larghezza']}x{m['altezza']}", [])
        if rates and max(rates) > m["frequenza"] + 5:
            out.append(f"{m['descrizione']} va a {m['frequenza']:g} Hz ma può arrivare a {max(rates):g} Hz.")
    return out
