"""Lo smistatore di Nova: un modello decisionale piccolo sceglie l'ambito della richiesta.

Idea: tanti modelli piccoli, ognuno al suo compito, invece di uno grande per tutto. Prima del modello
di conversazione, un modello decisionale (Tev1 0.8B di Together AI, via l'API System One di Ollama,
/v1/systemone) legge la frase e sceglie l'ambito: agenda, posta, file, impostazioni… Il modello di
conversazione riceve allora solo gli strumenti di quell'ambito: su un processore senza scheda video
leggere 15 strumenti invece di 90 vuol dire rispondere in pochi secondi invece che in decine, e un
modello piccolo sbaglia meno strumento.

Ordine dei livelli in Nova (agent.py): regole fisse (microsecondi) → smistatore (decine di ms) →
modello di conversazione con gli strumenti dell'ambito → se serve, tutti gli strumenti.
Se lo smistatore non c'è, non risponde o è incerto, il modello riceve gli strumenti di base (CORE).
Chi decide, in ordine: Laya (decisore.py, il System One addestrato per Nova: ambito, azione e percorso in
un colpo solo), il nucleo (nucleo.py: lo 0.8B con gli adattatori LoRA di AIOS, che compila anche i campi),
Tev1 in Ollama.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

DEFAULT_MODEL = "tev1:0.8b"
# Il modello piccolo che compila i campi di un'azione già scelta (formato JSON rigido): 2× più veloce del 2B.
FILL_MODEL = "qwen3.5:0.8b"
MIN_ACTION_CONFIDENCE = 0.6
NO_ACTION = "conversazione"
MIN_CONFIDENCE = 0.55  # sotto: si danno gli strumenti di base (CORE) più quelli dell'ambito incerto
TIMEOUT = 8.0
RETRY_AFTER = 60.0  # dopo un errore del modello decisionale si riprova fra un minuto
# Se lo smistatore non sa o non risponde, il modello di conversazione riceve solo questi (più quelli
# dell'ambito più probabile): con tutti gli strumenti le istruzioni sono ~10.000 parole e su un
# processore senza scheda video servono minuti solo per leggerle.
CORE = {"launch_app", "search_apps", "open_location", "search_files", "search_web", "set_volume", "set_brightness",
        "add_reminder", "list_agenda", "show_photos", "list_windows", "switch_window", "close_window", "go_home",
        "media_control", "take_screenshot", "edit_image", "where_left_off", "system_info"}


QUESTION_AMBITO = "Which area does this request to a personal computer assistant belong to?"
QUESTION_AZIONE = "Which action should the assistant run for this request?"
# Il percorso: comando da eseguire, risposta veloce a parole, o ragionamento (il modello pensa prima di
# rispondere). Uguale in uso e in addestramento (addestramento/dati_laya.py).
QUESTION_PERCORSO = "How should the assistant handle this request?"
PERCORSI = {
    "azione": "Do something on this computer or phone: open, set, search, play, show, remind, send, install",
    "risposta": "Just answer in words, quickly: chat, greetings, a simple question, short advice or a short text",
    "ragionamento": "Think carefully before answering: calculations, logic problems, programming code, long or "
                    "careful writing, comparing options, planning, explaining something complex",
}
QUESTION_PERCORSO_FULL = {"type": "choice", "instructions": QUESTION_PERCORSO, "criteria": PERCORSI}


def action_criteria(candidates: dict[str, Any]) -> dict[str, str]:
    """Le azioni possibili descritte per il modello decisionale (uguali in uso e in addestramento)."""
    criteria = {n: t.description[:200] for n, t in candidates.items()}
    criteria[NO_ACTION] = "None of these actions: the user wants to talk, asks a question or wants something else"
    return criteria


@dataclass
class Domain:
    name: str
    description: str  # quello che legge il modello decisionale
    tools: set[str] = field(default_factory=set)


# Gli ambiti di Nova: la descrizione è in inglese perché i modelli decisionali sono addestrati così,
# ma la frase dell'utente resta in italiano. Ogni ambito ha al massimo una decina di strumenti: su un
# processore lento ogni strumento in più sono secondi di attesa.
DOMAINS: list[tuple[str, str]] = [
    ("agenda", "Calendar, appointments, reminders, alarms, deadlines, daily summary, what's planned today or tomorrow"),
    ("posta", "Email: read, search, summarize, write or reply to mail, inbox, mail accounts"),
    ("file", "Find, open or read files, documents and photos on this computer: bills, contracts, shopping list, diet, "
             "photos of a person, place or period"),
    ("riordino", "Tidy up and organize files and folders, clean up space, collections of files"),
    ("app", "Applications and windows: open, close, install, remove or switch between programs"),
    ("impostazioni", "Volume, brightness, keyboard layout, Wi-Fi, Bluetooth, dark or light theme, music playback control, screenshot, lock, shut down or restart"),
    ("computer", "Time and date, battery and energy saving, system information, voice listening, opening a folder or place"),
    ("aggiornamenti", "System updates: check, install, from USB stick or GitHub, roll back to the previous version, switch to the NVIDIA graphics driver version"),
    ("chiamate", "Phone calls and SMS: answer, reject, read or send messages, make the phone ring, phone notifications"),
    ("telefono", "Link or unlink the phone, send files or photos to and from the phone, install AIOS on a phone, "
                 "see and control the user's other PCs (remote screen), send files to another PC"),
    ("identita", "The user's AIOS identity and account, syncing between devices, recovery phrase, revoking a device"),
    ("memoria", "The user's own past: where they left off, what they worked on yesterday or another day, a website or "
                "document they saw days ago, resuming a past conversation, the activity diary"),
    ("web", "Search the internet, websites, news, weather, facts that need online lookup"),
    ("ai", "AI models on this device, looking at images or the screen, dictation, reading aloud, creating images"),
    ("gusti", "Subscriptions, recommendations for music, films, series, books, what to watch or listen"),
    ("aspetto", "Themes, wallpapers, colors and look of the system, widgets on the home screen (weather, map, clock, note)"),
    ("chiacchiera", "General conversation, greetings, questions of knowledge, advice, writing help, anything not about this device"),
]
# Strumenti assegnati per nome; gli altri seguono il loro gruppo (vedi GROUP_DOMAIN).
DOMAIN_TOOLS: dict[str, set[str]] = {
    "impostazioni": {"set_volume", "set_brightness", "set_radio", "set_theme", "media_control", "take_screenshot",
                     "lock_screen", "power", "bluetooth_devices", "forget_bluetooth", "set_keyboard", "edit_image"},
    "computer": {"current_time", "system_info", "energy_choice", "energy_status", "voice_listening", "voice_status",
                 "open_location"},
    "aggiornamenti": {"auto_updates", "connect_github_updates", "update_from_usb", "update_now", "update_status", "switch_system_variant",
                      "restart_to_update", "rollback_system"},
    "chiamate": {"answer_call", "reject_call", "read_sms", "send_sms", "reply_message", "ring_phone", "setup_calls",
                 "phone_notifications"},
    "identita": {"create_identity", "identity_status", "restore_identity", "revoke_device", "show_recovery_phrase",
                 "sync_now", "set_relay"},
    "riordino": {"cleanup_suggestions", "tidy_apply", "tidy_plan", "tidy_undo", "list_collections", "show_collection"},
}
GROUP_DOMAIN = {"sistema": "computer"}


class Smistatore:
    """Chiede al modello decisionale l'ambito di una frase; None se non sa o non può."""

    def __init__(self, domains: list[Domain], model: str | None = None, url: str | None = None,
                 post: Callable[[str, dict[str, Any], float], dict[str, Any]] | None = None,
                 nucleo: Any = "predefinito", decisore: Any = None):
        self.domains = {d.name: d for d in domains}
        if nucleo == "predefinito":
            from .nucleo import Nucleo

            nucleo = None if os.environ.get("AIOS_NUCLEO", "") == "spento" or post is not None else Nucleo()
        self.nucleo = nucleo  # un modello con gli adattatori (nucleo.py): se c'è, al posto di Tev1 e del 0.8B
        self.model = model or os.environ.get("AIOS_SMISTATORE", DEFAULT_MODEL)
        self.url = (url or os.environ.get("AIOS_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.post = post or _post
        self._off_until = 0.0  # dopo un errore (modello assente, Ollama vecchio) si riprova più tardi
        self.last: dict[str, Any] = {}
        self._cache: tuple[str, tuple[str, float] | None] | None = None
        self.fill_model = os.environ.get("AIOS_MODELLO_CAMPI", FILL_MODEL)
        if decisore is None:
            from .decisore import Decisore

            # con un `post` di prova si parla solo con quello (Tev1 finto), non con un Laya vero
            decisore = Decisore(post=self.post, laya_url="" if post is not None else None, ollama_url=self.url,
                                tev1=self.model)
        self.decisore = decisore

    def decide(self, text: str) -> tuple[str, float] | None:
        if time.monotonic() < self._off_until or not text.strip():
            return None
        if self._cache and self._cache[0] == text:  # la stessa frase: piano e strumenti usano la stessa scelta
            return self._cache[1]
        decided = self._decide(text)
        self._cache = (text, decided)
        return decided

    def _ask(self, text: str, name: str, instructions: str, criteria: dict[str, str],
             only: str | None = None) -> tuple[str, float] | None:
        return self.decisore.choose(text[:2000], name, {"type": "choice", "instructions": instructions,
                                                         "criteria": criteria}, only=only)

    def percorso(self, text: str) -> tuple[str, float] | None:
        """Comando, risposta veloce o ragionamento (lo dice Laya insieme all'ambito). Senza Laya: None, subito
        (le scorciatoie restano istantanee)."""
        if not self.decisore.available("laya"):
            return None
        self.decide(text)
        p = self.last.get("percorso")
        return (p, float(self.last.get("fiducia_percorso", 0.0))) if p else None

    def _nucleo(self) -> Any:
        return self.nucleo if self.nucleo is not None and self.nucleo.available() else None

    def question_ambito(self) -> dict[str, Any]:
        """La domanda «di che ambito è?» per il modello decisionale (uguale in uso e in addestramento)."""
        return {"type": "choice", "instructions": QUESTION_AMBITO,
                "criteria": {name: d.description for name, d in self.domains.items()}}

    def _decide(self, text: str) -> tuple[str, float] | None:
        if self.decisore.available("laya"):
            answers = self.decisore.ask(text[:2000], {"ambito": self.question_ambito(),
                                                      "percorso": QUESTION_PERCORSO_FULL}, only="laya")
            try:
                a, p = answers["ambito"], answers["percorso"]  # type: ignore[index]
                choice, confidence = str(a["choice"]), float(a.get("confidence", 0.0))
                self.last = {"ambito": choice, "fiducia": confidence, "da": "laya", "percorso": str(p["choice"]),
                             "fiducia_percorso": float(p.get("confidence", 0.0))}
                if choice in self.domains:
                    return choice, confidence
            except (KeyError, TypeError, ValueError):
                pass
        nucleo = self._nucleo()
        if nucleo is not None:
            from .nucleo import prompt_ambito

            got = nucleo.choose(prompt_ambito(text), list(self.domains))
            if got is not None:
                self.last = {"ambito": got[0], "fiducia": got[1], "da": "nucleo"}
                return got
        got = self._ask(text, "ambito", QUESTION_AMBITO, self.question_ambito()["criteria"], only="tev1")
        if got is None:
            return None
        choice, confidence = got
        self.last = {"ambito": choice, "fiducia": confidence}
        if choice not in self.domains:
            return None
        return choice, confidence

    # --- divisione dei compiti: azione scelta da Tev1, campi compilati dal modello piccolo -----------
    def plan(self, text: str, tools: dict[str, Any], now: Callable[[], Any] | None = None) -> tuple[str, dict[str, Any]] | None:
        """(strumento, argomenti) se l'azione è chiara, senza il modello grande; None per lasciar fare a lui."""
        decided = self.decide(text)
        if decided is None or decided[1] < MIN_CONFIDENCE or decided[0] == "chiacchiera":
            return None
        candidates = {n: tools[n] for n in sorted(self.domains[decided[0]].tools) if n in tools}
        if not candidates or len(candidates) > 25:
            return None
        picked = None
        if self.decisore.available("laya"):
            picked = self._ask(text, "azione", QUESTION_AZIONE, action_criteria(candidates), only="laya")
        nucleo = self._nucleo()
        if picked is None and nucleo is not None:
            from .nucleo import prompt_azione

            names = [*candidates, NO_ACTION]
            picked = nucleo.choose(prompt_azione(text, names), names)
        if picked is None:
            picked = self._ask(text, "azione", QUESTION_AZIONE, action_criteria(candidates), only="tev1")
        if picked is None or picked[0] not in candidates or picked[1] < MIN_ACTION_CONFIDENCE:
            return None
        tool = candidates[picked[0]]
        props = tool.parameters.get("properties", {})
        if not props:
            return tool.name, {}
        args = self.fill(text, tool, now)
        required = set(tool.parameters.get("required", []))
        if args is None or not required <= {k for k, v in args.items() if v not in ("", None)}:
            return None
        return tool.name, {k: v for k, v in args.items() if k in props}

    def fill(self, text: str, tool: Any, now: Callable[[], Any] | None = None) -> dict[str, Any] | None:
        """I campi di un'azione dalla frase dell'utente: JSON rigido (schema dello strumento) dal modello piccolo."""
        from datetime import datetime

        when = (now or datetime.now)()
        nucleo = self._nucleo()
        if nucleo is not None:
            from .nucleo import prompt_campi

            props = tool.parameters.get("properties", {})
            got = nucleo.fill(prompt_campi(text, tool.name, tool.description, props, when), props,
                              tool.parameters.get("required", []))
            if got is not None:
                return fix_dates(got, props, text, when)
        schema = {"type": "object", "properties": tool.parameters.get("properties", {}),
                  "required": tool.parameters.get("required", [])}
        payload = {"model": self.fill_model, "stream": False, "think": False, "format": schema, "keep_alive": "30m",
                   "options": {"temperature": 0},
                   "messages": [{"role": "system", "content": f"Adesso è {when:%A %d/%m/%Y %H:%M}. Estrai dalla frase "
                                 f"dell'utente i valori per l'azione «{tool.description}». Rispondi solo con il JSON; "
                                 "lascia vuoto un campo che la frase non dice."},
                                {"role": "user", "content": text[:1000]}]}
        try:
            reply = self.post(f"{self.url}/api/chat", payload, 30.0)
            args = json.loads(reply["message"]["content"])
            return args if isinstance(args, dict) else None
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
            return None

    def narrow(self, text: str) -> set[str] | None:
        """Gli strumenti da dare al modello di conversazione: quelli dell'ambito, o quelli di base se è incerto."""
        decided = self.decide(text)
        if decided is None:
            return set(CORE)
        if decided[1] < MIN_CONFIDENCE:
            return CORE | set(self.domains[decided[0]].tools)
        return set(self.domains[decided[0]].tools)


def fix_dates(args: dict[str, Any], props: dict[str, Any], text: str, now: Any) -> dict[str, Any]:
    """Le date le calcola when.py (regole fisse, mai sbagliate su «domani» o «tra un'ora»), non il modello:
    il modello piccolo sceglie bene i campi ma a volte sbaglia il giorno."""
    iso = [k for k, spec in props.items() if "ISO" in str(spec.get("description", ""))]
    if not iso:
        return args
    from .when import parse_when

    found = parse_when(text, now)
    if found.at is None:
        return args
    value = found.at.strftime("%Y-%m-%d") if found.all_day else found.at.strftime("%Y-%m-%dT%H:%M")
    return {**args, iso[0]: value}


def _post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def build(groups: dict[str, list[Any]], always: set[str] | None = None, **kw: Any) -> Smistatore:
    """Gli ambiti con gli strumenti veri: groups = {"agenda": [Tool, …], …}. `always`: strumenti sempre presenti."""
    always = always or set()
    assigned = {t for tools in DOMAIN_TOOLS.values() for t in tools}
    by_domain: dict[str, set[str]] = {name: set(DOMAIN_TOOLS.get(name, set())) for name, _ in DOMAINS}
    present = set()
    for group, tools in groups.items():
        for t in tools:
            present.add(t.name)
            if t.name not in assigned:
                by_domain.setdefault(GROUP_DOMAIN.get(group, group), set()).add(t.name)
    domains = [Domain(name, description, (by_domain.get(name, set()) & present) | always) for name, description in DOMAINS]
    return Smistatore(domains, **kw)
