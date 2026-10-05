"""Lo schermo a voce: luce notturna («accendi la luce notturna», «schermo più caldo»), e i monitor
(risoluzione, frequenza, scala, rotazione, disposizione) — vedi monitor.py e luce_notturna.py."""

from __future__ import annotations

import re
from typing import Any

from .. import luce_notturna as LN
from .. import monitor as MON
from ..fastpath import Intent, normalize
from .base import Tool, params


def make_tools(monitors: Any = None) -> list[Tool]:
    def screens() -> MON.Monitors:
        return monitors or MON.Monitors()

    def list_displays() -> str:
        state = screens().state()
        if not state:
            return "Non riesco a leggere gli schermi (serve la sessione AIOS con Hyprland)."
        lines = [MON.describe(m) + " Può: " + ", ".join(
            f"{r.replace('x', '×')} fino a {max(f):g} Hz" for r, f in list(m["risoluzioni"].items())[:4]) for m in state]
        return "\n".join(lines + MON.best_rate_advice(state))

    def set_display(schermo: str = "", risoluzione: str = "", frequenza: float = 0, scala: float = 0, rotazione: str = "") -> str:
        changes: dict[str, Any] = {}
        if risoluzione:
            changes["risoluzione"] = risoluzione.replace("×", "x").replace(" ", "")
        if frequenza:
            changes["frequenza"] = float(frequenza)
        if scala:
            changes["scala"] = float(scala) / 100 if float(scala) > 4 else float(scala)
        if rotazione:
            changes["rotazione"] = {"normale": 0, "90": 1, "verticale": 1, "180": 2, "capovolto": 2, "270": 3}.get(str(rotazione).lower(), 0)
        if not changes:
            return list_displays()
        if changes.get("frequenza") == -1:  # «al massimo»
            st = next(iter(screens().state()), None)
            changes["frequenza"] = max(st["risoluzioni"].get(f"{st['larghezza']}x{st['altezza']}", [60])) if st else 60
        ok, msg = screens().change(schermo, changes, confirm=False)
        return msg + (" Se qualcosa non va: Impostazioni › Schermo › Torna alle scelte automatiche." if ok else "")

    def night_light(stato: str = "accendi", temperatura: int = 0, modo: str = "") -> str:
        stato = (stato or "accendi").lower()
        changes: dict[str, Any] = {}
        if temperatura:
            changes["temperatura"] = temperatura
        if modo in ("sole", "orari"):
            changes["modo"] = modo
        if stato in ("spegni", "off", "no"):
            LN.save({**changes, "attiva": False, "fino_a": ""})
            return "Luce notturna spenta: lo schermo torna normale."
        if stato in ("adesso", "ora", "subito"):
            LN.save(changes)
            conf = LN.now_on()
            return f"Luce notturna accesa adesso, fino alle {conf['fino_a'][11:16]}."
        if stato in ("piu calda", "più calda", "calda"):
            changes["temperatura"] = LN.settings()["temperatura"] - 500
        elif stato in ("meno calda", "piu fredda", "più fredda"):
            changes["temperatura"] = LN.settings()["temperatura"] + 500
        LN.save({**changes, "attiva": True})
        st = LN.status()
        when = "dal tramonto all'alba" if st["modo"] == "sole" else f"dalle {st['inizio']} alle {st['fine']}"
        now = " È già accesa." if st["accesa_ora"] else ""
        return f"Luce notturna attiva {when} (stasera dalle {st['da']}), a {st['temperatura']} K.{now}"

    return [
        Tool("list_displays", "Gli schermi collegati: risoluzione, frequenza (Hz), scala, e cosa possono fare.", params(), list_displays),
        Tool("set_display", "Cambia uno schermo: risoluzione (es. 1920x1080), frequenza in Hz (es. 144; -1 = la più alta), "
             "scala in percento (100, 125, 150…), rotazione (normale, verticale, capovolto).",
             params(schermo="Quale schermo (facoltativo se ce n'è uno)", risoluzione="es. 2560x1440", frequenza="Hz",
                    scala="Percento", rotazione="normale, verticale, capovolto"), set_display),
        Tool("night_light", "Luce notturna (meno luce blu la sera): accendi (in automatico dal tramonto all'alba o con orari), "
             "spegni, adesso (subito fino a domattina), più calda o meno calda.",
             params(stato=("Cosa fare", ["accendi", "spegni", "adesso", "più calda", "meno calda"]),
                    temperatura="Temperatura in kelvin, 2500-5500 (facoltativa)",
                    modo=("Quando (facoltativo)", ["sole", "orari"])), night_light),
    ]


RE_NIGHT = re.compile(r"^(?P<v>accendi|attiva|spegni|disattiva|togli|metti)\s+(?:la\s+)?(?:luce\s+notturna|modalita\s+notte|modalità\s+notte|filtro\s+(?:della\s+)?luce\s+blu)"
                      r"(?P<adesso>\s+(?:adesso|ora|subito))?$")
RE_WARM = re.compile(r"^(?:rendi\s+)?(?:lo\s+)?schermo\s+(?P<w>piu\s+caldo|più\s+caldo|meno\s+caldo|piu\s+freddo|più\s+freddo)$"
                     r"|^luce\s+notturna\s+(?P<w2>piu\s+calda|più\s+calda|meno\s+calda)$")


RE_HZ = re.compile(r"^(?:metti|imposta|porta)\s+(?:lo\s+schermo|il\s+monitor|la\s+frequenza(?:\s+dello\s+schermo)?)\s+(?:a|al)\s+"
                   r"(?:(?P<n>\d{2,3})\s*(?:hz|hertz)|(?P<max>massimo|la\s+frequenza\s+massima))$")
RE_RES = re.compile(r"^(?:metti|imposta|cambia)\s+(?:la\s+)?risoluzione\s+(?:a\s+|in\s+)?(?P<w>\d{3,4})\s*[x×per ]+\s*(?P<h>\d{3,4})$")
RE_SCALE = re.compile(r"^(?:ingrandisci|rimpicciolisci|metti|imposta)\s+(?:tutto|lo\s+schermo|la\s+scala(?:\s+dello\s+schermo)?)\s+(?:a|al)\s+(?P<p>\d{2,3})\s*(?:%|per\s*cento)$")
RE_LIST = re.compile(r"^(?:che|quali)\s+(?:schermi|monitor)\s+(?:ho|ci\s+sono|sono\s+collegati)|^a\s+quanti\s+hz\s+va\s+(?:lo\s+schermo|il\s+monitor)")


class DisplayRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        m = RE_HZ.match(low)
        if m:
            return Intent("set_display", {"frequenza": -1 if m.group("max") else int(m.group("n"))})
        m = RE_RES.match(low)
        if m:
            return Intent("set_display", {"risoluzione": f"{m.group('w')}x{m.group('h')}"})
        m = RE_SCALE.match(low)
        if m:
            return Intent("set_display", {"scala": int(m.group("p"))})
        if RE_LIST.search(low):
            return Intent("list_displays", {})
        m = RE_NIGHT.match(low)
        if m:
            off = m.group("v") in ("spegni", "disattiva", "togli")
            return Intent("night_light", {"stato": "spegni" if off else ("adesso" if m.group("adesso") else "accendi")})
        m = RE_WARM.match(low)
        if m:
            w = (m.group("w") or m.group("w2")).replace("piu", "più")
            return Intent("night_light", {"stato": "più calda" if w.startswith("più cald") else "meno calda"})
        return None
