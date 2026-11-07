"""Notifiche, SMS e rubrica del telefono sul PC (tramite il demone KDE Connect, D-Bus).

KDE Connect porta sul PC le notifiche del telefono, le conversazioni SMS e i contatti
(sincronizzati in ~/.local/share/kpeoplevcard). SoIA ci aggiunge l'intelligenza:
messaggi delle persone separati dal resto, codici di verifica riconosciuti e copiati,
risposte e SMS dal copilota (sempre con conferma prima di inviare).
Tutto resta sul PC: nulla passa da servizi esterni.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..tools.base import Runner

SERVICE = "org.kde.kdeconnect"
DEVICES = "/modules/kdeconnect/devices"
NOTIF_IFACE = "org.kde.kdeconnect.device.notifications"
CONV_IFACE = "org.kde.kdeconnect.device.conversations"

# App di messaggistica: le loro notifiche sono persone che scrivono.
MESSAGING = ("whatsapp", "telegram", "signal", "messaggi", "messages", "sms", "messenger", "instagram", "threema",
             "element", "discord", "slack", "teams", "viber", "line", "wechat", "google chat")
CALLS = ("telefono", "phone", "dialer", "chiamate")
OTP = re.compile(r"(?i)(?:codice|code|otp|pin|password monouso|verifica|verification|conferma|accesso|login)\D{0,40}?"
                 r"(?<!\d)(\d{4,8})(?!\d)|(?<!\d)(\d{4,8})(?!\d)\D{0,30}?(?:è il tuo codice|is your(?: \w+)?(?: verification)? code)")


@dataclass
class PhoneNotification:
    id: str
    app: str
    title: str
    text: str
    reply_id: str = ""
    dismissable: bool = True

    @property
    def kind(self) -> str:
        if otp_code(f"{self.title} {self.text}"):
            return "codice"
        app = self.app.lower()
        if any(m in app for m in MESSAGING):
            return "messaggio"
        if any(c in app for c in CALLS):
            return "chiamata"
        return "altro"


@dataclass
class Sms:
    thread: int
    address: str
    body: str
    date: datetime
    incoming: bool
    read: bool = True
    name: str = ""

    @property
    def who(self) -> str:
        return self.name or self.address


def otp_code(text: str) -> str:
    m = OTP.search(text)
    return (m.group(1) or m.group(2)) if m else ""


def _plain(value):
    """busctl --json: {"type": .., "data": ..} → valore."""
    return value.get("data") if isinstance(value, dict) and "data" in value else value


class PhoneBus:
    def __init__(self, runner: Runner | None = None):
        self.runner = runner or Runner()

    def _busctl(self, *args: str) -> object | None:
        code, out = self.runner.run(["busctl", "--user", "--json=short", *args])
        if code != 0:
            return None
        try:
            return json.loads(out.splitlines()[0]) if out.strip() else None
        except ValueError:
            return None

    def _call(self, path: str, iface: str, method: str, signature: str = "", *args: str) -> object | None:
        extra = [signature, *args] if signature else []
        reply = self._busctl("call", SERVICE, path, iface, method, *extra)
        return reply.get("data") if isinstance(reply, dict) else None

    def _prop(self, path: str, iface: str, name: str):
        return _plain(self._busctl("get-property", SERVICE, path, iface, name))

    # --- notifiche -------------------------------------------------------------------------------
    def notifications(self, device: str) -> list[PhoneNotification]:
        path = f"{DEVICES}/{device}/notifications"
        data = self._call(path, NOTIF_IFACE, "activeNotifications")
        ids = data[0] if data else []
        found = []
        for nid in ids:
            npath = f"{path}/{nid}"
            iface = f"{NOTIF_IFACE}.notification"
            get = lambda name: self._prop(npath, iface, name)  # noqa: E731
            found.append(PhoneNotification(str(nid), str(get("appName") or ""), str(get("title") or ""),
                                           str(get("text") or get("ticker") or ""), str(get("replyId") or ""),
                                           bool(get("dismissable"))))
        return found

    def reply(self, device: str, reply_id: str, message: str) -> bool:
        return self._call(f"{DEVICES}/{device}/notifications", NOTIF_IFACE, "sendReply", "ss", reply_id, message) is not None

    def dismiss(self, device: str, notification_id: str) -> bool:
        path = f"{DEVICES}/{device}/notifications/{notification_id}"
        return self._call(path, f"{NOTIF_IFACE}.notification", "dismiss") is not None

    # --- SMS -------------------------------------------------------------------------------------
    def conversations(self, device: str, contacts: dict[str, str] | None = None) -> list[Sms]:
        """L'ultimo messaggio di ogni conversazione, dal più recente."""
        path = f"{DEVICES}/{device}"
        self._call(path, CONV_IFACE, "requestAllConversationThreads")
        data = self._call(path, CONV_IFACE, "activeConversations")
        messages = [m for m in (parse_message(v) for v in (data[0] if data else [])) if m]
        for m in messages:
            m.name = (contacts or {}).get(normalize_number(m.address), "")
        return sorted(messages, key=lambda m: m.date, reverse=True)


def parse_message(value) -> Sms | None:
    """Messaggio di KDE Connect (ConversationMessage): evento, testo, indirizzi, data, tipo, letto, conversazione…"""
    fields = _plain(value)
    if not isinstance(fields, list) or len(fields) < 7:
        return None
    try:
        body = str(_plain(fields[1]))
        addresses = _plain(fields[2]) or []
        address = ""
        if addresses:
            first = _plain(addresses[0])
            address = str(_plain(first[0]) if isinstance(first, list) else first)
        date = datetime.fromtimestamp(int(_plain(fields[3])) / 1000)
        kind = int(_plain(fields[4]))  # 1 ricevuto, 2 inviato (come Android)
        read = bool(_plain(fields[5]))
        thread = int(_plain(fields[6]))
    except (TypeError, ValueError, IndexError):
        return None
    return Sms(thread, address, body, date, kind == 1, read)


def normalize_number(number: str) -> str:
    digits = re.sub(r"[^\d+]", "", number)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    return digits[-9:]  # ultime cifre: «+39 333…» e «333…» sono lo stesso numero


def contacts_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "kpeoplevcard"


def load_contacts(base: Path | None = None) -> dict[str, str]:
    """Rubrica del telefono sincronizzata da KDE Connect: numero normalizzato → nome."""
    contacts: dict[str, str] = {}
    for vcf in (base or contacts_dir()).glob("**/*.vcf"):
        try:
            text = vcf.read_text(errors="replace")
        except OSError:
            continue
        for card in text.split("BEGIN:VCARD")[1:]:
            name = re.search(r"^FN[^:]*:(.+)$", card, re.M)
            for tel in re.findall(r"^TEL[^:]*:(.+)$", card, re.M):
                if name and normalize_number(tel):
                    contacts[normalize_number(tel)] = name.group(1).strip()
    return contacts


def find_number(who: str, contacts: dict[str, str]) -> tuple[str, str] | None:
    """Da un nome (anche parziale) o un numero → (numero, nome)."""
    if re.fullmatch(r"[\d +()-]{6,}", who.strip()):
        return who.strip(), contacts.get(normalize_number(who), "")
    wanted = who.lower().strip()
    exact = [(n, name) for n, name in contacts.items() if name.lower() == wanted]
    partial = [(n, name) for n, name in contacts.items() if wanted in name.lower()]
    names = {name for _, name in (exact or partial)}
    if len(names) != 1:
        return None  # nessuno o ambiguo: meglio chiedere che scrivere alla persona sbagliata
    return (exact or partial)[0]


def summarize(notifications: list[PhoneNotification]) -> str:
    if not notifications:
        return "Sul telefono non ci sono notifiche nuove."
    groups = {"codice": [], "messaggio": [], "chiamata": [], "altro": []}
    for n in notifications:
        groups[n.kind].append(n)
    lines = []
    for n in groups["codice"]:
        lines.append(f"🔑 Codice {otp_code(n.title + ' ' + n.text)} da {n.app or n.title} (dimmi «copia il codice»).")
    if groups["messaggio"]:
        lines.append("💬 Messaggi:")
        lines += [f"  {n.title} ({n.app}): {n.text[:120]}" + (" — puoi rispondere da qui" if n.reply_id else "")
                  for n in groups["messaggio"]]
    if groups["chiamata"]:
        lines.append("📞 " + "; ".join(f"{n.title} {n.text}".strip() for n in groups["chiamata"]))
    if groups["altro"]:
        apps = {}
        for n in groups["altro"]:
            apps[n.app or "altro"] = apps.get(n.app or "altro", 0) + 1
        lines.append("Altro: " + ", ".join(f"{app} ({k})" if k > 1 else app for app, k in apps.items()))
    return "\n".join(lines)
