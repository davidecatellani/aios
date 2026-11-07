"""L'audio a voce: «fai uscire l'audio dal monitor», «usa le cuffie», «usa il microfono della webcam»,
«abbassa Firefox al 30», «che uscite audio ci sono?»."""

from __future__ import annotations

import re
from typing import Any, Callable

from ..audio import Audio
from ..fastpath import Intent, normalize
from .base import Tool, params


def make_tools(audio: Callable[[], Audio] = Audio) -> list[Tool]:
    def set_audio_output(dispositivo: str) -> str:
        return audio().route(dispositivo, output=True)

    def set_audio_input(dispositivo: str) -> str:
        return audio().route(dispositivo, output=False)

    def set_app_volume(programma: str, livello: int) -> str:
        return audio().app_volume(programma, int(livello))

    def list_audio_devices() -> str:
        st = audio().state()

        def line(e: Any) -> str:
            extra = " ← in uso" if e.predefinito else " (spenta, si può attivare)" if e.da_attivare else ""
            return f"• {e.nome}{extra}"

        parts = ["Uscite:", *([line(e) for e in st["uscite"]] or ["• nessuna"]),
                 "Microfoni:", *([line(e) for e in st["ingressi"]] or ["• nessuno"])]
        if st["programmi"]:
            parts += ["Stanno suonando: " + ", ".join(s.programma for s in st["programmi"])]
        return "\n".join(parts)

    return [
        Tool("set_audio_output", "Sceglie da dove esce il suono: monitor (HDMI), casse, cuffie, Bluetooth, USB o il nome "
             "del dispositivo. Accende anche un'uscita spenta (es. l'HDMI della scheda video).",
             params(dispositivo="Quale uscita: monitor, casse, cuffie, bluetooth, o il nome", required=["dispositivo"]),
             set_audio_output),
        Tool("set_audio_input", "Sceglie il microfono: quello del PC, della webcam, delle cuffie, USB o per nome.",
             params(dispositivo="Quale microfono", required=["dispositivo"]), set_audio_input),
        Tool("set_app_volume", "Cambia il volume di un solo programma che sta suonando (es. Firefox, Spotify).",
             params(programma="Il programma", livello="Volume da 0 a 150", required=["programma", "livello"]), set_app_volume),
        Tool("list_audio_devices", "Elenca le uscite audio, i microfoni e i programmi che stanno suonando.", params(),
             list_audio_devices),
    ]


TARGET = r"((?:il |lo |la |le |l'|gli |i )?(?:monitor|schermo|tv|televisore|hdmi|displayport|cuffie|auricolari|casse|" \
         r"altoparlanti|bluetooth|usb|webcam|microfono\w*)(?:\s+[\w'-]+){0,3})"
RE_OUT = re.compile(r"^(?:fai uscire|manda|metti|sposta|porta)\s+(?:l'audio|il suono|l'uscita audio|la musica)\s+"
                    r"(?:dal|dallo|dalla|dalle|dagli|sul|sullo|sulla|sulle|sugli|nel|nello|nella|nelle|negli|in|su|al|alle)\s+" + TARGET + r"$"
                    r"|^(?:usa|passa a(?:lle|l|i|gli)?|attiva)\s+" + TARGET + r"\s+(?:per l'audio|per il suono|come uscita)$"
                    r"|^(?:audio|suono)\s+(?:sul|sullo|sulla|sulle|nel|nelle|dal|dalle)\s+" + TARGET + r"$")
RE_IN = re.compile(r"^(?:usa|passa a(?:l)?|attiva|scegli)\s+(?:il\s+)?microfono\s+(?:del|della|dello|delle|di|dell')\s*(?P<dove>.+)$")
RE_APP = re.compile(r"^(?:metti|porta|abbassa|alza|imposta)\s+(?:il volume di|il volume del|il volume della|)\s*(?P<app>[a-z0-9 .+-]+?)"
                    r"\s+(?:al|a)\s+(?P<n>\d{1,3})\s*(?:%|per ?cento)?$")
RE_LIST = re.compile(r"^(?:quali|che)\s+(?:uscite|dispositivi)\s+audio\b|^(?:da dove esce|dove esce)\s+(?:l'audio|il suono)")


class AudioRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        if RE_LIST.search(low):
            return Intent("list_audio_devices", {})
        m = RE_IN.match(low)
        if m:
            return Intent("set_audio_input", {"dispositivo": m.group("dove").strip()})
        m = RE_OUT.match(low)
        if m:
            return Intent("set_audio_output", {"dispositivo": next(g for g in m.groups() if g).strip()})
        m = RE_APP.match(low)
        if m and m.group("app").strip() not in ("volume", "il volume", "audio", "suono", "luminosita", "luminosità"):
            return Intent("set_app_volume", {"programma": m.group("app").strip(), "livello": int(m.group("n"))})
        return None
