"""Telefono e PC: collegare, far squillare, mandare file, rispondere alle chiamate."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from ..fastpath import Intent, normalize
from ..mesh import calls as calls_mod
from ..mesh.calls import Ofono
from ..mesh.files import Devices
from ..mesh.phone import KdeConnect
from ..mesh.service import send_command
from .base import Runner, Tool, params

NO_KDECONNECT = ("Per collegare il telefono serve KDE Connect sul PC (è nell'immagine di AIOS) e l'app "
                 "KDE Connect sul telefono (Android o iPhone), sulla stessa rete Wi-Fi.")


def qr_text(url: str, runner: Runner) -> str:
    if not runner.has("qrencode"):
        return ""
    code, out = runner.run(["qrencode", "-t", "UTF8", "-m", "1", url])
    return out if code == 0 else ""


def make_tools(runner: Runner | None = None, command: Callable[[dict[str, Any]], dict[str, Any] | None] = send_command,
               devices: Callable[[], Devices] = Devices) -> list[Tool]:
    runner = runner or Runner()
    kc, ofono = KdeConnect(runner), Ofono(runner)

    def phone_status() -> str:
        lines = []
        if kc.available():
            phones = kc.devices()
            near = [p.name for p in phones if p.paired and p.reachable]
            far = [p.name for p in phones if p.paired and not p.reachable]
            lines.append("Vicini e collegati: " + (", ".join(near) or "nessuno"))
            if far:
                lines.append("Abbinati ma lontani: " + ", ".join(far))
        else:
            lines.append(NO_KDECONNECT)
        files = [d.name for d in devices().items]
        lines.append("Possono aprire i file del PC: " + (", ".join(files) or "nessun telefono (dimmi «collega il telefono»)"))
        call = _safe(ofono.incoming)
        if call:
            lines.append(f"📞 Chiamata in arrivo da {call.who}.")
        return "\n".join(lines)

    def connect_phone() -> str:
        parts = []
        if kc.available():
            new = [p for p in kc.devices() if p.reachable and not p.paired]
            for p in new:
                kc.pair(p)
            if new:
                parts.append(f"Ho chiesto l'abbinamento a {', '.join(p.name for p in new)}: conferma sul telefono "
                             "(una volta sola). Da lì in poi si collega da solo quando è vicino.")
            elif not kc.nearby():
                parts.append("Non vedo telefoni: apri l'app KDE Connect sul telefono, sulla stessa rete Wi-Fi del PC.")
        else:
            parts.append(NO_KDECONNECT)
        reply = command({"azione": "abbina"})
        if reply is None or "url" not in reply:
            parts.append("Per aprire i file del PC dal telefono avvia il servizio: systemctl --user enable --now aios-telefono")
            return "\n".join(parts)
        url = reply["url"]
        qr = qr_text(url, runner)
        parts.append("Per aprire i file del PC dal telefono, inquadra questo codice con la fotocamera (vale 5 minuti):")
        parts.append(qr or url)
        if qr:
            parts.append(url)
        parts.append("Il browser avviserà che il certificato è del tuo PC: è normale, accettalo una volta.")
        return "\n".join(parts)

    def ring_phone(name: str = "") -> str:
        phone = kc.find(name)
        if phone is None:
            return "Non vedo il telefono vicino. " + ("" if kc.available() else NO_KDECONNECT)
        return f"Faccio squillare {phone.name}." if kc.ring(phone) else "Non sono riuscito a farlo squillare."

    def send_to_phone(path: str, name: str = "") -> str:
        phone = kc.find(name)
        if phone is None:
            return "Non vedo il telefono vicino."
        file = Path(path).expanduser()
        if file.is_file():
            return f"Mandato {file.name} a {phone.name}." if kc.share(phone, file) else "Invio non riuscito."
        return f"Mandato a {phone.name}." if kc.send_text(phone, path) else "Invio non riuscito."

    def answer_call() -> str:
        return ofono.answer()

    def reject_call() -> str:
        return ofono.hang_up()

    def setup_calls() -> str:
        errors = calls_mod.setup_hands_free(runner)
        if errors:
            return "Non sono riuscito a completare la configurazione:\n" + "\n".join(errors)
        return ("Fatto: ora il PC può fare da vivavoce. Ultimo passo, una volta sola: abbina il telefono al PC in Bluetooth "
                "(Impostazioni → Bluetooth). Poi le chiamate arrivano anche qui e puoi dirmi «rispondi».")

    def forget_phone(name: str) -> str:
        removed = devices().remove(name)
        unpaired = [p.name for p in kc.devices() if p.paired and name.lower() in p.name.lower() and kc.unpair(p)] \
            if kc.available() else []
        if not removed and not unpaired:
            return f"Non ho trovato un telefono «{name}»."
        return f"Scollegato «{name}»: non potrà più aprire i file del PC né ricevere notifiche finché non lo ricolleghi."

    return [
        Tool("phone_status", "Mostra i telefoni collegati, quelli abbinati e le chiamate in arrivo.", params(), phone_status),
        Tool("connect_phone", "Collega un telefono al PC (abbinamento KDE Connect e codice QR per aprire i file del PC "
             "dal telefono).", params(), connect_phone),
        Tool("ring_phone", "Fa squillare il telefono per ritrovarlo.", params([], name="Nome del telefono"), ring_phone),
        Tool("send_to_phone", "Manda un file (percorso) o un testo/link al telefono.",
             params(["path"], path="Percorso del file o testo", name="Nome del telefono"), send_to_phone),
        Tool("answer_call", "Risponde dal PC alla chiamata in arrivo sul telefono.", params(), answer_call),
        Tool("reject_call", "Rifiuta o chiude la chiamata del telefono.", params(), reject_call),
        Tool("setup_calls", "Prepara il PC a rispondere alle chiamate del telefono (vivavoce Bluetooth).", params(),
             setup_calls, requires_confirmation=True),
        Tool("forget_phone", "Scollega un telefono: non potrà più aprire i file del PC.", params(name="Nome del telefono"),
             forget_phone, requires_confirmation=True),
    ]


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


PHONE = r"(?:il\s+(?:mio\s+)?)?(?:telefono|cellulare|smartphone)"
RE_CONNECT = re.compile(rf"^(?:collega|abbina|connetti|aggiungi)\s+{PHONE}(?:\s+al\s+(?:pc|computer))?$"
                        r"|^(?:voglio\s+)?(?:aprire|vedere)\s+i\s+file\s+del\s+(?:pc|computer)\s+(?:dal|sul)\s+telefono$")
RE_RING = re.compile(rf"^(?:fai|fa)\s+squillare\s+{PHONE}$|^(?:dov'è|dove\s+è|trova)\s+{PHONE}\??$|^non\s+trovo\s+{PHONE}$")
RE_ANSWER = re.compile(r"^rispondi(?:\s+(?:al\s+telefono|alla\s+chiamata|dal\s+pc))?$")
RE_REJECT = re.compile(r"^(?:rifiuta|riaggancia|chiudi|termina)(?:\s+(?:la\s+)?chiamata)?$|^riattacca$")
RE_STATUS = re.compile(rf"^(?:i\s+)?miei\s+dispositivi$|^{PHONE}\s+è\s+collegato\??$|^stato\s+del\s+telefono$")
RE_SEND = re.compile(rf"^(?:manda|invia|passa)\s+(?P<what>.+?)\s+al\s+telefono$")
RE_SETUP_CALLS = re.compile(r"^(?:voglio\s+)?rispondere\s+(?:alle\s+chiamate|al\s+telefono)\s+dal\s+(?:pc|computer)$"
                            r"|^(?:attiva|configura)\s+(?:le\s+)?chiamate\s+(?:sul|dal)\s+(?:pc|computer)$")


class PhoneRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text)
        if RE_CONNECT.match(low):
            return Intent("connect_phone", {})
        if RE_RING.match(low):
            return Intent("ring_phone", {})
        if RE_ANSWER.match(low):
            return Intent("answer_call", {})
        if RE_REJECT.match(low):
            return Intent("reject_call", {})
        if RE_STATUS.match(low):
            return Intent("phone_status", {})
        if RE_SETUP_CALLS.match(low):
            return Intent("setup_calls", {})
        m = RE_SEND.match(low)
        if m:
            path = re.search(r"[/~]\S+", text)
            if path:
                return Intent("send_to_phone", {"path": path.group(0)})
        return None
