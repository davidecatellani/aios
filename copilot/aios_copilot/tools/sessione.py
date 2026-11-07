"""Riprendere da dove eri: «riapri quello che avevo aperto», «cosa avevo aperto sull'altro computer?»,
«riapri quello che avevo aperto sul portatile» (vedi sessione.py)."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Callable

from .. import sessione
from ..fastpath import Intent, normalize
from .base import Tool, params


def _launch(cmd: list[str]) -> bool:
    if not shutil.which(cmd[0]):
        return False
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def make_tools(sessions: Callable[[], sessione.Sessions] = sessione.Sessions,
               apps: Callable[[], dict[str, Any]] | None = None,
               windows: Callable[[], list[dict[str, str]]] | None = None,
               launch: Callable[[list[str]], bool] = _launch, boot: Callable[[], float] | None = None) -> list[Tool]:
    def _apps() -> dict[str, Any]:
        if apps is not None:
            return apps()
        from ..shell import installed_apps

        return installed_apps()

    def _windows() -> list[dict[str, str]]:
        if windows is not None:
            return windows()
        from ..shell import open_windows

        return open_windows()

    def _boot() -> float:
        if boot is not None:
            return boot()
        from ..shell import boot_time

        return boot_time()

    def _pick_remote(dispositivo: str) -> tuple[str, dict[str, Any]] | None:
        others = sessions().remote()
        if not others:
            return None
        key = dispositivo.lower().strip()
        named = [(d, s) for d, s in others.items() if key and key in d.lower()]
        return (named or sorted(others.items(), key=lambda t: -t[1].get("quando", 0)))[0]

    def restore_session(dispositivo: str = "") -> str:
        store = sessions()
        if dispositivo.strip():
            picked = _pick_remote(dispositivo)
            if picked is None:
                return "Non ho ancora niente dagli altri dispositivi: serve l'identità di SoIA collegata su entrambi."
            device, snap = picked
            done = sessione.restore(snap, _apps(), launch, _windows(), same_device=False)
            return (f"Riaperto da {device}: {', '.join(done)}." if done else
                    f"Su {device} avevi {sessione.describe(snap)}, ma qui non ho niente da riaprire (app non installate?).")
        snap = store.last_session()
        if snap is None:
            return "Non ho una sessione salvata da riaprire."
        store.mark_offered(_boot())
        done = sessione.restore(snap, _apps(), launch, _windows(), same_device=True)
        return f"Riaperto: {', '.join(done)}." if done else "Era già tutto aperto."

    def skip_session() -> str:
        sessions().mark_offered(_boot())
        return "Va bene, niente riapertura."

    def other_devices() -> str:
        others = sessions().remote()
        if not others:
            return "Non ho sessioni degli altri dispositivi (serve l'identità di SoIA collegata e sincronizzata)."
        lines = [f"• {d}: {sessione.describe(s) or 'niente di aperto'}" for d, s in others.items()]
        return "Sugli altri dispositivi:\n" + "\n".join(lines) + "\nDimmi «riapri quello che avevo aperto su …»."

    return [
        Tool("restore_session", "Riapre i programmi, i file e i siti che erano aperti prima dello spegnimento, "
             "o quelli aperti su un altro dispositivo dell'utente.",
             params([], dispositivo="Nome dell'altro dispositivo (facoltativo; vuoto = questo computer)"), restore_session),
        Tool("skip_session", "Non riaprire la sessione precedente.", params(), skip_session),
        Tool("other_devices_session", "Dice cosa è aperto sugli altri dispositivi dell'utente.", params(), other_devices),
    ]


RE_RESTORE = re.compile(r"^(?:riapri|riprendi|rimetti)\s+(?:tutto|quello\s+che\s+avevo\s+aperto|(?:le\s+)?(?:app|finestre|programmi)"
                        r"(?:\s+(?:di\s+prima|che\s+avevo\s+aperto))?|la\s+sessione(?:\s+di\s+prima)?)"
                        r"(?:\s+(?:sul|sull'|nel|dal|dall')\s*(?P<d>.+))?$")
RE_SKIP = re.compile(r"^(?:non\s+riaprire|lascia\s+stare)\s+(?:la\s+sessione|niente|quello\s+di\s+prima)$")
RE_OTHERS = re.compile(r"^(?:cosa|che\s+cosa)\s+(?:avevo|ho)\s+aperto\s+(?:sul|sull'|nel)\s*(?:l')?(?:altro|altri)\b.*$")


class SessionRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text).strip(" .!?")
        if RE_SKIP.match(low):
            return Intent("skip_session", {})
        if RE_OTHERS.match(low):
            return Intent("other_devices_session", {})
        m = RE_RESTORE.match(low)
        if m:
            device = (m.group("d") or "").strip()
            device = re.sub(r"^(?:l'|lo\s+|il\s+)?(?:altro\s+)?", "", device) or ("altro" if m.group("d") else "")
            return Intent("restore_session", {"dispositivo": device})
        return None
