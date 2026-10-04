"""Modalità gioco: quando parte un gioco AIOS si fa da parte e gli lascia memoria e processore.

Si accorge dei giochi dalle finestre (Hyprland): Steam (steam_app_…), gamescope, Wine/Proton (.exe), gli
emulatori più noti, e ogni app che nel suo file .desktop si dichiara gioco (Categories=Game). Allora:
- scarica i modelli di Ollama dalla memoria (il modello per parlare, Tev1, gli embedding…);
- ferma il nucleo (aios-nucleo: modello piccolo e adattatori);
- lascia un segno ($XDG_RUNTIME_DIR/aios-gioco) che ferma i lavori a riposo (foto, documenti) e fa usare
  all'ascolto solo Vosk, senza caricare Parakeet né il riconoscimento di chi parla.
Quando ricaricare: se l'ultimo gioco si chiude, dopo mezzo minuto (un caricamento tra due finestre non fa
ripartire tutto). Se il gioco resta aperto ma passa in secondo piano (ridotto a icona, si torna alla
schermata di AIOS o a un altro programma), decide il modello decisionale (Tev, API System One): «l'utente è
uscito un attimo dal gioco o sta facendo altro e gli servirà Nova?». Se il modello decisionale non c'è, una
regola: fuori dal gioco da 3 minuti e memoria libera a sufficienza. Tornando nel gioco, la memoria si libera
di nuovo. I modelli di Ollama si ricaricano da soli alla prima richiesta a Nova, che risponde sempre.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

GAME_CLASS = re.compile(r"^(?:steam_app_\d+|gamescope|.*\.exe|minecraft.*|retroarch|dolphin-emu|pcsx2.*|rpcs3|"
                        r"ryujinx|yuzu|citra.*|ppsspp.*|duckstation.*|supertuxkart|xonotic.*|openttd|0ad|"
                        r"lutris|heroic|steam_proton)$", re.IGNORECASE)
LEAVE_DELAY = 30.0  # secondi dopo la chiusura dell'ultimo gioco prima di ricaricare
AWAY_ASK = 45.0  # dopo quanti secondi fuori dal gioco si chiede al modello decisionale se ricaricare
ASK_EVERY = 60.0  # e ogni quanto si richiede, se ha detto di aspettare
RULE_AWAY = 180.0  # senza modello decisionale: ricarica dopo 3 minuti fuori dal gioco…
RULE_FREE_GB = 2.5  # …se c'è almeno questa memoria libera
# Fuori dai giochi i modelli di Nova restano caricati (risposte senza attese); se però la memoria libera scende
# sotto questa soglia (un programma pesante), il modello di conversazione si scarica e si ricarica alla
# prossima domanda (qualche secondo). Laya resta: è piccolo e ricaricarlo costa di più.
LOW_FREE_GB = 0.8
GUARD_EVERY = 60.0
OLLAMA = os.environ.get("AIOS_OLLAMA_URL", "http://localhost:11434").rstrip("/")


def flag() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "aios-gioco"


def active() -> bool:
    """C'è un gioco aperto? (per i lavori a riposo e per l'ascolto)."""
    return flag().exists()


def is_game(window_class: str, apps: dict[str, Any] | None = None) -> bool:
    cls = window_class.strip()
    if not cls:
        return False
    if GAME_CLASS.match(cls):
        return True
    for app_id, app in (apps or {}).items():
        if getattr(app, "game", False) and (app_id.lower() == cls.lower() or app_id.lower().endswith("." + cls.lower())
                                             or getattr(app, "name", "").lower() == cls.lower()):
            return True
    return False


def _post(url: str, payload: dict[str, Any]) -> Any:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read() or b"{}")


def unload_ollama() -> list[str]:
    """Scarica dalla memoria tutti i modelli di Ollama (si ricaricano da soli quando servono)."""
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=5) as resp:
            loaded = [m["name"] for m in json.loads(resp.read()).get("models", [])]
    except (OSError, ValueError, KeyError):
        return []
    for name in loaded:
        try:
            _post(f"{OLLAMA}/api/generate", {"model": name, "keep_alive": 0})
        except (OSError, ValueError):
            pass
    return loaded


def nucleo(action: str) -> bool:
    """Ferma o riavvia aios-nucleo (la regola polkit di AIOS lo permette all'utente seduto al computer)."""
    try:
        return subprocess.run(["systemctl", action, "--no-block", "aios-nucleo.service"], capture_output=True,
                              timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def free_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 2**20
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


# la domanda per il modello decisionale (uguale in uso e in addestramento, addestramento/dati_laya.py)
RELOAD_QUESTION = {"type": "choice", "instructions": "Should the assistant reload its AI models now?",
                   "criteria": {"si": "The user left the game to do something else on the computer and will "
                                      "probably need the assistant soon",
                                "no": "The user only stepped out of the game for a moment (a menu, a quick "
                                      "message) and will go back to playing"}}


def reload_state(game: str, away: float, free: float, clock: str) -> str:
    return (f"Un gioco ({game}) è aperto ma non è in primo piano da {int(away // 60)} minuti e {int(away % 60)} "
            f"secondi: l'utente lo ha ridotto a icona o è passato a un altro programma. Memoria libera: {free:.1f} GB. "
            f"Ora: {clock}.")


def decide_reload(game: str, away: float, free: float, ask: Callable[[str, dict[str, Any]], Any] | None = None) -> bool:
    """Il gioco è in secondo piano: ricaricare Nova adesso? Lo decide il modello decisionale (System One, quello dello smistatore);
    se non risponde, una regola semplice."""
    state = reload_state(game, away, free, time.strftime("%H:%M"))
    question = {"ricarica": RELOAD_QUESTION}
    try:
        if ask is None:
            from .smistatore import Smistatore

            q = question["ricarica"]
            got = Smistatore([])._ask(state, "ricarica", q["instructions"], q["criteria"])
            answer = {"choice": got[0], "confidence": got[1]} if got else None
        else:
            answer = ask(state, question)
        if answer and float(answer.get("confidence", 0.0)) >= 0.6:
            return str(answer["choice"]) == "si"
    except Exception:
        pass
    return away >= RULE_AWAY and free >= RULE_FREE_GB


class GameMode:
    def __init__(self, apps: Callable[[], dict[str, Any]] | None = None,
                 unload: Callable[[], list[str]] = unload_ollama, service: Callable[[str], bool] = nucleo,
                 notify: Callable[[str], None] | None = None, clock: Callable[[], float] = time.monotonic,
                 decide: Callable[[str, float, float], bool] = lambda g, away, free: decide_reload(g, away, free),
                 free: Callable[[], float] = free_gb):
        self.free = free
        self.apps = apps or (lambda: {})
        self.unload, self.service, self.notify, self.clock = unload, service, notify, clock
        self.games: dict[str, str] = {}  # indirizzo della finestra → classe
        self._left_at: float | None = None
        self._away_since: float | None = None  # il gioco è aperto ma in secondo piano da…
        self._asked_at = float("-inf")
        self._guard_at = float("-inf")
        self.decide = decide
        self._lock = threading.Lock()

    @property
    def on(self) -> bool:
        return flag().exists()

    def handle(self, event: str, data: str) -> None:
        """Gli eventi di Hyprland (socket2): openwindow>>indirizzo,spazio,classe,titolo / closewindow>>indirizzo."""
        with self._lock:
            if event == "openwindow":
                parts = data.split(",", 3)
                if len(parts) >= 3 and is_game(parts[2], self.apps()):
                    self.games[parts[0]] = parts[2]
                    self._left_at = None
                    if not self.on:
                        self.enter(parts[2])
            elif event == "closewindow" and data.strip() in self.games:
                self.games.pop(data.strip())
                if not self.games:
                    self._left_at = self.clock()
                    self._away_since = None
            elif event == "activewindowv2" and self.games:
                if data.strip() in self.games:  # di nuovo nel gioco: si libera la memoria (se era stata ricaricata)
                    self._away_since = None
                    if not self.on:
                        self.enter(self.games[data.strip()], quiet=True)
                elif self._away_since is None:
                    self._away_since = self.clock()  # ridotto a icona, schermata di AIOS, un altro programma

    def tick(self) -> None:
        """Da chiamare ogni tanto: esce dalla modalità gioco mezzo minuto dopo la chiusura dell'ultimo gioco."""
        with self._lock:
            now = self.clock()
            if not self.on and now - self._guard_at >= GUARD_EVERY and self.free() < LOW_FREE_GB:
                self._guard_at = now
                self.unload()  # memoria quasi finita: il modello di conversazione lascia il posto
            if self.on and not self.games and self._left_at is not None and now - self._left_at >= LEAVE_DELAY:
                self._left_at = None
                self.leave()
            elif self.on and self.games and self._away_since is not None:
                away = now - self._away_since
                if away >= AWAY_ASK and now - self._asked_at >= ASK_EVERY:
                    self._asked_at = now
                    game = next(iter(self.games.values()))
                    if self.decide(game, away, self.free()):
                        self.leave()  # il gioco resta aperto: tornandoci la memoria si libera di nuovo

    def enter(self, game: str, quiet: bool = False) -> None:
        try:
            flag().write_text(game)
        except OSError:
            pass
        freed = self.unload()
        self.service("stop")
        if self.notify and not quiet:
            self.notify("Modalità gioco: ho liberato la memoria" + (f" ({len(freed)} modelli)" if freed else "") +
                        ". Se mi chiami rispondo lo stesso.")

    def leave(self) -> None:
        try:
            flag().unlink()
        except OSError:
            pass
        self.service("start")


def run(mode: GameMode, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    while not stop.wait(5.0):
        mode.tick()
