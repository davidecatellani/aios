"""Lo smistatore di Nova: un modello decisionale piccolo sceglie l'ambito della richiesta.

Idea: tanti modelli piccoli, ognuno al suo compito, invece di uno grande per tutto. Prima del modello
di conversazione, un modello decisionale (Tev1 0.8B di Together AI, via l'API System One di Ollama,
/v1/systemone) legge la frase e sceglie l'ambito: agenda, posta, file, impostazioni… Il modello di
conversazione riceve allora solo gli strumenti di quell'ambito: su un processore senza scheda video
leggere 15 strumenti invece di 90 vuol dire rispondere in pochi secondi invece che in decine, e un
modello piccolo sbaglia meno strumento.

Ordine dei livelli in Nova (agent.py): regole fisse (microsecondi) → smistatore (decine di ms) →
modello di conversazione con gli strumenti dell'ambito → se serve, tutti gli strumenti.
Se lo smistatore non c'è, non risponde o è incerto, Nova lavora come prima, con tutti gli strumenti.
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
MIN_CONFIDENCE = 0.55  # sotto: si danno tutti gli strumenti (meglio lento che sbagliato)
TIMEOUT = 8.0


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
    ("file", "Find, open or read files and documents on this computer: bills, contracts, shopping list, diet"),
    ("riordino", "Tidy up and organize files and folders, clean up space, collections of files"),
    ("app", "Applications and windows: open, close, install, remove or switch between programs"),
    ("impostazioni", "Volume, brightness, keyboard layout, Wi-Fi, Bluetooth, dark or light theme, music playback control, screenshot, lock, shut down or restart"),
    ("computer", "Time and date, battery and energy saving, system information, voice listening, opening a folder or place"),
    ("aggiornamenti", "System updates: check, install, from USB stick or GitHub, roll back to the previous version"),
    ("chiamate", "Phone calls and SMS: answer, reject, read or send messages, make the phone ring, phone notifications"),
    ("telefono", "Link or unlink the phone, send files or photos to and from the phone, install AIOS on a phone"),
    ("identita", "The user's AIOS identity and account, syncing between devices, recovery phrase, revoking a device"),
    ("web", "Search the internet, websites, news, weather, facts that need online lookup"),
    ("ai", "AI models on this device, looking at images or the screen, dictation, reading aloud, creating images"),
    ("gusti", "Subscriptions, recommendations for music, films, series, books, what to watch or listen"),
    ("aspetto", "Themes, wallpapers, colors and look of the system"),
    ("chiacchiera", "General conversation, greetings, questions of knowledge, advice, writing help, anything not about this device"),
]
# Strumenti assegnati per nome; gli altri seguono il loro gruppo (vedi GROUP_DOMAIN).
DOMAIN_TOOLS: dict[str, set[str]] = {
    "impostazioni": {"set_volume", "set_brightness", "set_radio", "set_theme", "media_control", "take_screenshot",
                     "lock_screen", "power", "bluetooth_devices", "forget_bluetooth", "set_keyboard"},
    "computer": {"current_time", "system_info", "energy_choice", "energy_status", "voice_listening", "voice_status",
                 "open_location"},
    "aggiornamenti": {"auto_updates", "connect_github_updates", "update_from_usb", "update_now", "update_status",
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
                 post: Callable[[str, dict[str, Any], float], dict[str, Any]] | None = None):
        self.domains = {d.name: d for d in domains}
        self.model = model or os.environ.get("AIOS_SMISTATORE", DEFAULT_MODEL)
        self.url = (url or os.environ.get("AIOS_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.post = post or _post
        self._off_until = 0.0  # dopo un errore (modello assente, Ollama vecchio) si riprova più tardi
        self.last: dict[str, Any] = {}
        self._cache: tuple[str, tuple[str, float] | None] | None = None
        self.fill_model = os.environ.get("AIOS_MODELLO_CAMPI", FILL_MODEL)

    def decide(self, text: str) -> tuple[str, float] | None:
        if time.monotonic() < self._off_until or not text.strip():
            return None
        if self._cache and self._cache[0] == text:  # la stessa frase: piano e strumenti usano la stessa scelta
            return self._cache[1]
        decided = self._decide(text)
        self._cache = (text, decided)
        return decided

    def _ask(self, text: str, name: str, instructions: str, criteria: dict[str, str]) -> tuple[str, float] | None:
        payload = {"model": self.model, "state": text[:2000],
                   "questions": {name: {"type": "choice", "instructions": instructions, "criteria": criteria}}}
        try:
            answer = self.post(f"{self.url}/v1/systemone", payload, TIMEOUT)["answers"][name]
            return str(answer["choice"]), float(answer.get("confidence", 0.0))
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
            self._off_until = time.monotonic() + 300
            return None

    def _decide(self, text: str) -> tuple[str, float] | None:
        payload = {"model": self.model, "state": text[:2000], "questions": {"ambito": {
            "type": "choice", "instructions": "Which area does this request to a personal computer assistant belong to?",
            "criteria": {name: d.description for name, d in self.domains.items()}}}}
        try:
            reply = self.post(f"{self.url}/v1/systemone", payload, TIMEOUT)
            answer = reply["answers"]["ambito"]
            choice, confidence = str(answer["choice"]), float(answer.get("confidence", 0.0))
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
            self._off_until = time.monotonic() + 300
            return None
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
        criteria = {n: t.description[:200] for n, t in candidates.items()}
        criteria[NO_ACTION] = "None of these actions: the user wants to talk, asks a question or wants something else"
        picked = self._ask(text, "azione", "Which action should the assistant run for this request?", criteria)
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
        """Gli strumenti da dare al modello di conversazione, o None per darli tutti."""
        decided = self.decide(text)
        if decided is None or decided[1] < MIN_CONFIDENCE:
            return None
        return set(self.domains[decided[0]].tools)


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
