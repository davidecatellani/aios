"""Lo schermo a voce: luce notturna («accendi la luce notturna», «schermo più caldo»), e i monitor
(risoluzione, frequenza, scala, rotazione, disposizione) — vedi monitor.py e luce_notturna.py."""

from __future__ import annotations

import re
from typing import Any

from .. import accessibilita as A11Y
from .. import dispositivi as DEV
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

    def accessibility(opzione: str, acceso: bool = True) -> str:
        key = {"sottotitoli": "sottotitoli", "lettore": "lettore", "lettore dello schermo": "lettore", "orca": "lettore",
               "contrasto": "contrasto", "contrasto alto": "contrasto", "puntatore": "cursore_grande", "cursore": "cursore_grande",
               "animazioni": "meno_animazioni", "meno animazioni": "meno_animazioni"}.get(opzione.lower().strip())
        if opzione.lower().startswith("zoom"):
            factor = A11Y.zoom("+" if acceso else "0")
            return f"Zoom al {round(factor * 100)}%. Super e - per ridurre, Super e 0 per tornare normale."
        if opzione.lower() in A11Y.FILTERS or opzione.lower() in ("daltonismo", "filtro colore", "grigi", "bianco e nero"):
            name = {"daltonismo": "deuteranopia", "filtro colore": "deuteranopia", "bianco e nero": "grigi"}.get(opzione.lower(), opzione.lower())
            A11Y.apply({"filtro": name if acceso else ""})
            return f"Filtro colore: {A11Y.FILTERS[name if acceso else '']}."
        if key is None:
            return "Posso accendere o spegnere: sottotitoli, lettore dello schermo, contrasto alto, puntatore grande, meno animazioni, zoom, filtri colore."
        if key == "sottotitoli":
            from .windows import _shell

            _shell("--sottotitoli", "1" if acceso else "0")  # la striscia la disegna la shell
        _conf, msg = A11Y.apply({key: acceso})
        names = {"sottotitoli": "Sottotitoli in tempo reale", "lettore": "Lettore dello schermo", "contrasto": "Contrasto alto",
                 "cursore_grande": "Puntatore grande", "meno_animazioni": "Meno animazioni"}
        return f"{names[key]} {'acceso' if acceso else 'spento'}." + ("" if msg == "Fatto." else " " + msg)

    def mouse_settings(opzione: str, valore: str = "") -> str:
        o, v = opzione.lower().strip(), str(valore).lower().strip()
        cur = DEV.settings()
        on = v not in ("no", "false", "spento", "spegni", "0", "off")
        if o in ("velocita", "velocità", "velocita mouse", "velocità mouse"):
            new = cur["velocita"] + (0.25 if v in ("più", "piu", "+", "veloce", "aumenta") else -0.25 if v in ("meno", "-", "lento", "diminuisci") else 0)
            if v.replace(".", "", 1).lstrip("-").isdigit():
                new = float(v)
            DEV.apply({"velocita": new})
            return f"Velocità del puntatore: {round((max(-1, min(1, new)) + 1) * 50)}%."
        key = {"accelerazione": "accelerazione", "mano sinistra": "mano_sinistra", "scorrimento naturale": "scorrimento_naturale",
               "tocco per clic": "tocco_clic", "tap": "tocco_clic", "bloc num": "bloc_num"}.get(o)
        if key is None:
            return "Posso cambiare: velocità del puntatore, accelerazione, mano sinistra, scorrimento naturale, tocco per clic, Bloc Num."
        DEV.apply({key: on, **({"scorrimento_naturale_mouse": on} if key == "scorrimento_naturale" else {})})
        return f"{opzione.capitalize()} {'acceso' if on else 'spento'}."

    def add_shortcut(tasti: str, programma: str = "", richiesta: str = "") -> str:
        app_id = ""
        if programma:
            from ..shell import installed_apps

            low = programma.lower()
            app_id = next((a.id for a in installed_apps().values() if a.name.lower() == low or low in a.id.lower()), "")
            if not app_id:
                return f"Non trovo il programma «{programma}»."
        ok, msg = DEV.add_shortcut(tasti, app_id, richiesta)
        return msg

    return [
        Tool("mouse_settings", "Mouse e touchpad: velocità del puntatore (più/meno o -1…1), accelerazione, mano sinistra, "
             "scorrimento naturale, tocco per clic, Bloc Num all'avvio.",
             params(opzione="Cosa cambiare", valore="più, meno, sì, no o un numero", required=["opzione"]), mouse_settings),
        Tool("add_shortcut", "Crea una scorciatoia da tastiera (es. Super+M) che apre un programma o fa una richiesta a Nova.",
             params(tasti="La combinazione, es. Super+M", programma="Il programma da aprire (oppure)",
                    richiesta="La frase da chiedere a Nova", required=["tasti"]), add_shortcut),
        Tool("accessibility", "Accessibilità: accende o spegne sottotitoli in tempo reale, lettore dello schermo, contrasto alto, "
             "puntatore grande, meno animazioni, zoom, filtri colore per daltonismo.",
             params(opzione="Quale: sottotitoli, lettore, contrasto, puntatore, animazioni, zoom, daltonismo, grigi",
                    acceso="true per accendere, false per spegnere", required=["opzione"]), accessibility),
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


RE_A11Y = re.compile(r"^(?P<v>attiva|accendi|metti|spegni|disattiva|togli)\s+(?:i\s+|il\s+|lo\s+|la\s+)?"
                     r"(?P<o>sottotitoli(?:\s+in\s+tempo\s+reale)?|lettore\s+(?:dello\s+)?schermo|contrasto\s+alto|puntatore\s+(?:grande|più\s+grande)|zoom|filtro\s+(?:per\s+il\s+)?daltonismo)$")
RE_SHORTCUT = re.compile(r"^(?:crea|aggiungi|fai)\s+(?:una\s+)?scorciatoia\s+(?P<k>(?:super|ctrl|alt|shift|maiusc)(?:\s*\+\s*\w+)+)\s+"
                         r"(?:che\s+apre|per\s+aprire)\s+(?P<app>[\w .-]+)$"
                         r"|^(?:crea|aggiungi|fai)\s+(?:una\s+)?scorciatoia\s+(?P<k2>(?:super|ctrl|alt|shift|maiusc)(?:\s*\+\s*\w+)+)\s+(?:per|che\s+fa|che\s+dice)\s+[«\"]?(?P<req>.+?)[»\"]?$")
RE_MOUSE = re.compile(r"^(?:rendi\s+)?(?:il\s+)?(?:mouse|puntatore)\s+(?:piu|più)\s+(?P<v>veloce|lento)$")
RE_ZOOM = re.compile(r"^(?:ingrandisci|zooma)\s+(?:lo\s+schermo|lo\s+zoom)$")


class DisplayRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        m = RE_A11Y.match(low)
        if m:
            o = m.group("o")
            opt = "sottotitoli" if o.startswith("sottotitoli") else "lettore" if o.startswith("lettore") else \
                "contrasto" if o.startswith("contrasto") else "puntatore" if o.startswith("puntatore") else \
                "zoom" if o == "zoom" else "daltonismo"
            return Intent("accessibility", {"opzione": opt, "acceso": m.group("v") in ("attiva", "accendi", "metti")})
        m = RE_SHORTCUT.match(low)
        if m:
            if m.group("app"):
                return Intent("add_shortcut", {"tasti": m.group("k"), "programma": m.group("app").strip()})
            return Intent("add_shortcut", {"tasti": m.group("k2"), "richiesta": m.group("req").strip()})
        m = RE_MOUSE.match(low)
        if m:
            return Intent("mouse_settings", {"opzione": "velocità", "valore": "più" if m.group("v") == "veloce" else "meno"})
        if RE_ZOOM.match(low):
            return Intent("accessibility", {"opzione": "zoom", "acceso": True})
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
