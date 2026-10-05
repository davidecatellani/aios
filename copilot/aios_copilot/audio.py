"""L'audio del PC come in un sistema moderno: da dove esce, da dove entra, quanto forte, per ogni programma.

Legge PipeWire (pw-dump: un'istantanea in JSON di dispositivi, uscite, ingressi e programmi che suonano) e
comanda con wpctl. Le scelte (uscita e microfono predefiniti, volumi) le ricorda WirePlumber da solo, anche
dopo il riavvio.

- Uscite: casse, cuffie, monitor (HDMI/DisplayPort), Bluetooth, USB. Anche quelle «nascoste»: l'uscita HDMI di
  una scheda video spesso c'è ma è spenta (il profilo della scheda è un altro); qui compare come «da attivare»
  e sceglierla accende il profilo giusto e la rende predefinita.
- Ingressi: i microfoni (portatile, webcam, cuffie, USB), con il livello e la prova (registra 4 secondi e li
  fa riascoltare).
- Programmi: chi sta suonando adesso (Firefox, Spotify…), con il volume e il muto di ciascuno.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

Run = Callable[[list[str]], tuple[int, str]]
TEST_SOUND = "/usr/share/sounds/freedesktop/stereo/bell.oga"
KIND_ICON = {"monitor": "🖥️", "cuffie": "🎧", "bluetooth": "🎧", "usb": "🔌", "casse": "🔊", "microfono": "🎙️",
             "webcam": "📷"}


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 127, ""


@dataclass
class Endpoint:
    id: int
    nome: str
    tipo: str        # monitor | cuffie | bluetooth | usb | casse | microfono | webcam
    predefinito: bool = False
    livello: int | None = None
    muto: bool = False
    nodo: str = ""   # node.name
    da_attivare: dict[str, int] = field(default_factory=dict)  # {dispositivo, profilo}: uscita spenta da accendere

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "icona": KIND_ICON.get(self.tipo, "🔊")}


@dataclass
class Stream:
    id: int
    programma: str
    livello: int | None = None
    muto: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def kind_of(props: dict[str, Any], output: bool) -> str:
    text = " ".join(str(props.get(k, "")) for k in ("node.name", "node.description", "device.description",
                                                    "device.bus", "device.form-factor", "api.alsa.pcm.id")).lower()
    if "bluez" in text or "bluetooth" in text:
        return "bluetooth"
    if re.search(r"hdmi|displayport|\bdp\b", text):
        return "monitor"
    if re.search(r"headset|headphone|cuffi", text):
        return "cuffie"
    if "webcam" in text or "camera" in text:
        return "webcam"
    if "usb" in text:
        return "usb"
    return "casse" if output else "microfono"


def friendly(props: dict[str, Any], kind: str) -> str:
    """Un nome che si capisce: «Monitor VG245», «Cuffie Sony WH-1000», «Uscita ottica (S/PDIF)», «Casse»."""
    desc = str(props.get("node.description") or props.get("node.name") or "Audio")
    nick = str(props.get("node.nick") or "")
    text = f"{props.get('node.name', '')} {desc}".lower()
    if kind == "monitor":
        nick = "" if re.search(r"hdmi|displayport|audio|controller|digital", nick, re.I) else nick
        port = "DisplayPort" if re.search(r"displayport|\bdp\b", text) else "HDMI"
        return f"Monitor {nick} ({port})".replace("  ", " ") if nick else f"Monitor ({port})"
    if re.search(r"iec958|s/?pdif", text):
        return "Uscita ottica (S/PDIF)"
    if kind == "bluetooth":
        return desc
    name = re.sub(r"\s+(?:Analog|Digital)\s+(?:Stereo|Surround[\w. ]*|Mono)", "", desc)
    name = re.sub(r"\s*(?:High Definition Audio Controller|Audio Controller)\b", "", name)
    name = re.sub(r"\s+(?:Output|Input)$", "", name).strip(" -—")
    if re.fullmatch(r"(?:Built-in Audio|Audio interno)?\s*(?:\(.*\))?", name) or not name:
        return {"casse": "Casse o cuffie (presa del PC)", "microfono": "Microfono (presa del PC)"}.get(kind, "Audio del PC")
    return name


def parse_dump(text: str) -> dict[str, Any]:
    """L'istantanea di PipeWire → uscite, ingressi, programmi e i profili spenti che hanno un'uscita."""
    try:
        objects = json.loads(text)
    except ValueError:
        return {"uscite": [], "ingressi": [], "programmi": [], "registrano": []}
    defaults: dict[str, str] = {}
    devices: dict[int, dict[str, Any]] = {}
    sinks, sources, plays, records = [], [], [], []
    for o in objects if isinstance(objects, list) else []:
        kind = o.get("type", "")
        info = o.get("info") or {}
        props = info.get("props") or {}
        if kind.endswith("Metadata") and (o.get("props") or {}).get("metadata.name") == "default":
            for entry in o.get("metadata") or []:
                value = entry.get("value")
                if isinstance(value, dict) and entry.get("key") in ("default.audio.sink", "default.audio.source",
                                                                     "default.configured.audio.sink"):
                    defaults[entry["key"]] = str(value.get("name", ""))
        elif kind.endswith("Device") and props.get("media.class") == "Audio/Device":
            devices[o["id"]] = {"props": props, "params": info.get("params") or {}}
        elif kind.endswith("Node"):
            cls = props.get("media.class", "")
            if cls == "Audio/Sink":
                sinks.append((o["id"], props))
            elif cls == "Audio/Source" and not str(props.get("node.name", "")).endswith(".monitor"):
                sources.append((o["id"], props))
            elif cls == "Stream/Output/Audio":
                plays.append((o["id"], props))
            elif cls == "Stream/Input/Audio":
                records.append((o["id"], props))

    def endpoint(oid: int, props: dict[str, Any], output: bool) -> Endpoint:
        k = kind_of(props, output)
        key = "default.audio.sink" if output else "default.audio.source"
        return Endpoint(oid, friendly(props, k), k, props.get("node.name") == defaults.get(key), nodo=str(props.get("node.name", "")))

    outs = [endpoint(i, p, True) for i, p in sinks]
    ins = [endpoint(i, p, False) for i, p in sources]
    # uscite spente: profili della scheda con un'uscita (HDMI/DisplayPort/analogica) disponibili ma non attivi
    active_devices = {int(p.get("device.id", -1)) for _, p in sinks}
    for did, dev in devices.items():
        params = dev["params"]
        current = {p.get("index") for p in params.get("Profile") or []}
        for prof in params.get("EnumProfile") or []:
            name = str(prof.get("name", ""))
            if prof.get("index") in current or not name.startswith("output:") or prof.get("available") == "no":
                continue
            if "+input" in name:
                continue  # le varianti con il microfono: basta quella solo uscita
            if did in active_devices and not re.search(r"hdmi|analog", name):
                continue  # dalla stessa scheda: solo l'uscita del monitor o la presa per casse e cuffie
            props = {**dev["props"], "node.description": f"{dev['props'].get('device.description', 'Scheda audio')} — "
                     f"{prof.get('description', name)}", "node.name": f"profilo:{did}:{prof.get('index')}"}
            e = endpoint(-1, props, True)
            e.da_attivare = {"dispositivo": did, "profilo": int(prof.get("index", 0))}
            outs.append(e)
    def stream(oid: int, props: dict[str, Any]) -> Stream:
        return Stream(oid, str(props.get("application.name") or props.get("media.name") or props.get("node.name") or "Programma"))

    return {"uscite": outs, "ingressi": ins, "programmi": [stream(i, p) for i, p in plays],
            "registrano": [stream(i, p) for i, p in records],
            "scelta_utente": bool(defaults.get("default.configured.audio.sink"))}


def read_volume(target: str | int, run: Run = _run) -> tuple[int | None, bool]:
    code, out = run(["wpctl", "get-volume", str(target)])
    m = re.search(r"Volume:\s*([\d.]+)", out) if code == 0 else None
    return (round(float(m.group(1)) * 100) if m else None), "MUTED" in out


class Audio:
    def __init__(self, run: Run = _run, wait: Callable[[float], None] = time.sleep):
        self.run, self.wait = run, wait

    def state(self, volumes: bool = True) -> dict[str, Any]:
        code, out = self.run(["pw-dump"])
        data = parse_dump(out) if code == 0 else parse_dump("[]")
        if volumes:
            for e in data["uscite"] + data["ingressi"]:
                if e.id >= 0:
                    e.livello, e.muto = read_volume(e.id, self.run)
            for s in data["programmi"]:
                s.livello, s.muto = read_volume(s.id, self.run)
        return data

    def as_json(self) -> dict[str, Any]:
        st = self.state()
        return {k: [x.to_dict() for x in v] if isinstance(v, list) else v for k, v in st.items()}

    def auto_default(self) -> str | None:
        """All'avvio, se l'utente non ha mai scelto: niente uscita ottica (S/PDIF, IEC958), dove di solito non è
        collegato niente, se c'è un'alternativa (il monitor, le casse, le cuffie). → il nome scelto, o None."""
        st = self.state(volumes=False)
        if st.get("scelta_utente"):
            return None
        current = next((e for e in st["uscite"] if e.predefinito), None)
        if current is None or not re.search(r"iec958|s/?pdif|optical|ottic", f"{current.nodo} {current.nome}", re.I):
            return None
        better = [e for e in st["uscite"] if e.id >= 0 and not e.predefinito
                  and not re.search(r"iec958|s/?pdif", f"{e.nodo} {e.nome}", re.I)]
        rank = {"bluetooth": 0, "usb": 1, "cuffie": 2, "monitor": 3, "casse": 4}
        better.sort(key=lambda e: rank.get(e.tipo, 9))
        if not better:
            return None
        self.run(["wpctl", "set-default", str(better[0].id)])
        return better[0].nome

    def choose(self, endpoint_id: int | None = None, profile: dict[str, int] | None = None) -> tuple[bool, str]:
        """Rende predefinita un'uscita o un ingresso; per un'uscita spenta prima accende il suo profilo."""
        if profile:
            code, _ = self.run(["wpctl", "set-profile", str(int(profile["dispositivo"])), str(int(profile["profilo"]))])
            if code != 0:
                return False, "Non riesco ad attivare quell'uscita."
            for _ in range(10):  # il nuovo nodo compare in un attimo
                self.wait(0.3)
                new = [e for e in self.state(volumes=False)["uscite"] if e.id >= 0 and kind_of({"node.name": e.nodo}, True)]
                hit = next((e for e in new if e.tipo == "monitor" or e.nodo and str(profile["dispositivo"]) in e.nodo), None)
                fresh = [e for e in new if not e.predefinito]
                if hit or fresh:
                    endpoint_id = (hit or fresh[-1]).id
                    break
            if endpoint_id is None:
                return True, "Ho attivato l'uscita: se non la senti, sceglila di nuovo tra un attimo."
        if endpoint_id is None:
            return False, "Quale uscita?"
        code, _ = self.run(["wpctl", "set-default", str(int(endpoint_id))])
        return code == 0, "Fatto." if code == 0 else "Non ci sono riuscito."

    def volume(self, target: int, level: int) -> bool:
        level = max(0, min(150, int(level)))
        return self.run(["wpctl", "set-volume", "-l", "1.5", str(int(target)), f"{level}%"])[0] == 0

    def mute(self, target: int, muted: bool) -> bool:
        return self.run(["wpctl", "set-mute", str(int(target)), "1" if muted else "0"])[0] == 0

    def test_output(self, popen: Callable[..., Any] = subprocess.Popen) -> bool:
        try:
            popen(["pw-play", TEST_SOUND], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            return False

    def test_input(self, seconds: int = 4) -> tuple[bool, str]:
        """Registra qualche secondo dal microfono scelto e lo fa riascoltare."""
        path = Path(tempfile.gettempdir()) / "aios-prova-microfono.wav"
        code, _ = self.run(["timeout", str(seconds), "pw-record", str(path)])
        if not path.exists() or path.stat().st_size < 2000:
            return False, "Non ho sentito niente dal microfono: controlla che sia quello giusto e non sia muto."
        self.run(["pw-play", str(path)])
        path.unlink(missing_ok=True)
        return True, "Ecco come ti sente il microfono."

    # --- a voce: «fai uscire l'audio dal monitor», «usa le cuffie», «usa il microfono della webcam» ---
    def find(self, query: str, output: bool = True) -> Endpoint | None:
        q = query.lower()
        items = self.state(volumes=False)["uscite" if output else "ingressi"]
        wanted = next((k for k, words in WORDS.items() if any(w in q for w in words)), None)
        if wanted:
            hits = [e for e in items if e.tipo == wanted] or ([e for e in items if e.tipo == "bluetooth"] if wanted == "cuffie" else [])
            if hits:
                return next((e for e in hits if e.da_attivare == {}), hits[0])
        return next((e for e in items if any(w for w in re.findall(r"\w{3,}", q) if w in e.nome.lower())), None)

    def route(self, query: str, output: bool = True) -> str:
        e = self.find(query, output)
        if e is None:
            names = ", ".join(x.nome for x in self.state(volumes=False)["uscite" if output else "ingressi"]) or "nessuna"
            return f"Non trovo «{query}». {'Uscite' if output else 'Microfoni'} che vedo: {names}."
        ok, msg = self.choose(None if e.da_attivare else e.id, e.da_attivare or None)
        if not ok:
            return msg
        return (f"Fatto: l'audio esce da {e.nome}." if output else f"Fatto: ora ti ascolto con {e.nome}.")

    def app_volume(self, app: str, level: int) -> str:
        streams = self.state(volumes=False)["programmi"]
        hit = [s for s in streams if app.lower() in s.programma.lower()]
        if not hit:
            playing = ", ".join(s.programma for s in streams) or "nessuno"
            return f"«{app}» non sta suonando adesso. Programmi che suonano: {playing}."
        for s in hit:
            self.volume(s.id, level)
        return f"Volume di {hit[0].programma} al {level}%."


WORDS = {"monitor": ("monitor", "schermo", "hdmi", "displayport", "televis", "tv"),
         "cuffie": ("cuffi", "auricolar", "headset"), "bluetooth": ("bluetooth",),
         "casse": ("casse", "altoparlant", "speaker", "pc"), "webcam": ("webcam", "telecamera"), "usb": ("usb",)}
