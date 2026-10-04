"""Controlli rapidi del sistema: audio, luminosità, tema, radio, musica, schermo, energia.

Ogni azione prova i programmi standard dei desktop Linux (PipeWire, NetworkManager,
GNOME, KDE, wlroots) e usa il primo disponibile.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from .apps import find_desktop_entry
from .base import Runner, Tool, params

MUSIC_PLAYERS = ["rhythmbox", "lollypop", "elisa", "amberol", "spotify", "audacious", "vlc"]


def _report(result: tuple[int, str] | None, ok: str, missing: str) -> str:
    if result is None:
        return missing
    code, out = result
    return ok if code == 0 else f"Non ci sono riuscito ({code}): {out[-300:]}"


def make_tools(
    runner: Runner | None = None,
    find_desktop: Callable[[str], Path | None] = find_desktop_entry,
    pictures_dir: Callable[[], Path] | None = None,
) -> list[Tool]:
    runner = runner or Runner()

    def set_volume(action: str) -> str:
        sink, pa_sink = "@DEFAULT_AUDIO_SINK@", "@DEFAULT_SINK@"
        commands = {
            "up": (["wpctl", "set-volume", "-l", "1.0", sink, "10%+"], ["pactl", "set-sink-volume", pa_sink, "+10%"]),
            "down": (["wpctl", "set-volume", sink, "10%-"], ["pactl", "set-sink-volume", pa_sink, "-10%"]),
            "mute": (["wpctl", "set-mute", sink, "1"], ["pactl", "set-sink-mute", pa_sink, "1"]),
            "unmute": (["wpctl", "set-mute", sink, "0"], ["pactl", "set-sink-mute", pa_sink, "0"]),
        }
        if action not in commands:
            return f"Azione volume non valida: {action}"
        done = {"up": "Volume alzato.", "down": "Volume abbassato.", "mute": "Audio disattivato.", "unmute": "Audio riattivato."}
        return _report(runner.first(*commands[action]), done[action], "Nessun controllo audio trovato (wpctl/pactl).")

    def set_brightness(action: str) -> str:
        step = {"up": "10%+", "down": "10%-"}.get(action)
        if step is None:
            return f"Azione luminosità non valida: {action}"
        done = "Luminosità aumentata." if action == "up" else "Luminosità ridotta."
        return _report(runner.first(["brightnessctl", "set", step]), done, "Controllo luminosità non disponibile (brightnessctl).")

    def set_theme(mode: str) -> str:
        if mode not in ("dark", "light"):
            return f"Tema non valido: {mode}"
        scheme = "prefer-dark" if mode == "dark" else "prefer-light"  # AIOS è scuro se non si sceglie il chiaro
        kde = "BreezeDark" if mode == "dark" else "BreezeLight"
        done = "Tema scuro attivato." if mode == "dark" else "Tema chiaro attivato."
        return _report(
            runner.first(
                ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", scheme],
                ["plasma-apply-colorscheme", kde],
            ),
            done,
            "Non so cambiare il tema su questo desktop.",
        )

    def set_radio(device: str, state: str) -> str:
        if device not in ("wifi", "bluetooth") or state not in ("on", "off"):
            return f"Richiesta non valida: {device} {state}"
        label = "Wi-Fi" if device == "wifi" else "Bluetooth"
        done = f"{label} {'attivato' if state == 'on' else 'disattivato'}."
        if device == "wifi":
            result = runner.first(["nmcli", "radio", "wifi", state])
        else:
            result = runner.first(
                ["bluetoothctl", "power", state],
                ["rfkill", "unblock" if state == "on" else "block", "bluetooth"],
            )
        return _report(result, done, f"Non trovo un modo per controllare il {label}.")

    def media_control(action: str) -> str:
        if action not in ("play", "pause", "play-pause", "next", "previous"):
            return f"Azione non valida: {action}"
        if not runner.has("playerctl"):
            return "Controllo multimediale non disponibile (playerctl)."
        code, _ = runner.run(["playerctl", action])
        if code == 0:
            return {"play": "Riproduzione avviata.", "pause": "In pausa.", "play-pause": "Fatto.",
                    "next": "Brano successivo.", "previous": "Brano precedente."}[action]
        if action == "play":
            # Nessun lettore aperto: apro la prima app musicale installata.
            for player in MUSIC_PLAYERS:
                entry = find_desktop(player)
                if entry is not None:
                    runner.spawn(["gtk-launch", entry.stem] if runner.has("gtk-launch") else [player])
                    return f"Nessuna musica in corso: apro {entry.stem}."
            return "Non trovo un lettore musicale: chiedimi di installarne uno."
        return "Non c'è nessun lettore multimediale attivo."

    def take_screenshot() -> str:
        folder = (pictures_dir() if pictures_dir else Path.home() / "Pictures") / "Screenshots"
        path = folder / f"Screenshot {datetime.now():%Y-%m-%d %H-%M-%S}.png"
        if runner.has("grim"):
            folder.mkdir(parents=True, exist_ok=True)
        return _report(
            runner.first(
                ["gnome-screenshot", "-f", str(path)],
                ["spectacle", "-b", "-n", "-f", "-o", str(path)],
                ["grim", str(path)],
            ),
            f"Screenshot salvato in {path}.",
            "Nessun programma per screenshot trovato.",
        )

    def lock_screen() -> str:
        return _report(runner.first(["loginctl", "lock-session"]), "Schermo bloccato.", "Non riesco a bloccare lo schermo.")

    def power(action: str) -> str:
        if action not in ("suspend", "poweroff", "reboot"):
            return f"Azione non valida: {action}"
        done = {"suspend": "Sospensione…", "poweroff": "Spegnimento…", "reboot": "Riavvio…"}[action]
        return _report(runner.first(["systemctl", action]), done, "systemctl non disponibile.")

    return [
        Tool("set_volume", "Alza, abbassa o disattiva l'audio.",
             params(action=("Cosa fare", ["up", "down", "mute", "unmute"])), set_volume),
        Tool("set_brightness", "Regola la luminosità dello schermo.",
             params(action=("Cosa fare", ["up", "down"])), set_brightness),
        Tool("set_theme", "Attiva il tema scuro o chiaro del sistema.",
             params(mode=("Tema", ["dark", "light"])), set_theme),
        Tool("set_radio", "Accende o spegne Wi-Fi o Bluetooth.",
             params(device=("Dispositivo", ["wifi", "bluetooth"]), state=("Stato", ["on", "off"])), set_radio),
        Tool("media_control", "Controlla la musica o il video in riproduzione; 'play' apre un lettore se nessuno è attivo.",
             params(action=("Comando", ["play", "pause", "play-pause", "next", "previous"])), media_control),
        Tool("take_screenshot", "Cattura lo schermo e salva l'immagine.", params(), take_screenshot),
        Tool("lock_screen", "Blocca lo schermo.", params(), lock_screen),
        Tool("power", "Sospende, spegne o riavvia il dispositivo.",
             params(action=("Azione", ["suspend", "poweroff", "reboot"])), power, requires_confirmation=True),
    ]
