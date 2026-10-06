"""Telefono e PC: collegare, far squillare, mandare file, rispondere alle chiamate."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable

from ..fastpath import Intent, normalize
from ..mesh import calls as calls_mod
from ..mesh.calls import Ofono
from ..mesh import messages as msg
from ..mesh.files import Devices
from ..mesh.phone import KdeConnect
from ..mesh.service import send_command
from .base import Runner, Tool, params

NO_KDECONNECT = ("Per collegare il telefono serve KDE Connect sul PC (è nell'immagine di SoIA) e l'app "
                 "KDE Connect sul telefono (Android o iPhone), sulla stessa rete Wi-Fi.")


def qr_text(url: str, runner: Runner) -> str:
    if not runner.has("qrencode"):
        return ""
    code, out = runner.run(["qrencode", "-t", "UTF8", "-m", "1", url])
    return out if code == 0 else ""


def copy_to_clipboard(text: str, runner: Runner) -> bool:
    for cmd in (["wl-copy", text], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        if runner.has(cmd[0]):
            if cmd[0] == "wl-copy":
                return runner.run(cmd)[0] == 0
            import subprocess

            try:
                subprocess.run(cmd, input=text, text=True, timeout=5, check=True)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def make_tools(runner: Runner | None = None, command: Callable[[dict[str, Any]], dict[str, Any] | None] = send_command,
               devices: Callable[[], Devices] = Devices,
               contacts: Callable[[], dict[str, str]] = msg.load_contacts) -> list[Tool]:
    runner = runner or Runner()
    kc, ofono, bus = KdeConnect(runner), Ofono(runner), msg.PhoneBus(runner)

    def phone_notifications() -> str:
        phone = kc.find()
        if phone is None:
            return "Il telefono non è vicino: non vedo le sue notifiche."
        return msg.summarize(bus.notifications(phone.id))

    def copy_code() -> str:
        phone = kc.find()
        codes = [(n, msg.otp_code(f"{n.title} {n.text}")) for n in (bus.notifications(phone.id) if phone else [])]
        codes = [(n, c) for n, c in codes if c]
        if not codes:
            return "Non vedo codici di verifica tra le notifiche del telefono."
        n, code = codes[-1]
        if copy_to_clipboard(code, runner):
            return f"Codice {code} ({n.app or n.title}) copiato: incollalo con Ctrl+V."
        return f"Il codice è {code} ({n.app or n.title})."

    def reply_message(who: str, text: str) -> str:
        phone = kc.find()
        if phone is None:
            return "Il telefono non è vicino."
        wanted = who.lower().strip()
        candidates = [n for n in bus.notifications(phone.id) if n.reply_id and n.kind == "messaggio"
                      and (wanted in n.title.lower() or wanted in n.app.lower())]
        if not candidates:
            return f"Non vedo un messaggio di «{who}» a cui rispondere dal PC."
        n = candidates[-1]
        return f"Risposto a {n.title} su {n.app}." if bus.reply(phone.id, n.reply_id, text) else "Risposta non riuscita."

    def read_sms(who: str = "") -> str:
        phone = kc.find()
        if phone is None:
            return "Il telefono non è vicino: non posso leggere gli SMS."
        book = contacts()
        conversations = bus.conversations(phone.id, book)
        if who:
            wanted = who.lower().strip()
            match = msg.find_number(who, book)
            conversations = [m for m in conversations if wanted in m.who.lower()
                             or (match and msg.normalize_number(m.address) == msg.normalize_number(match[0]))]
        if not conversations:
            return f"Nessun SMS{' di ' + who if who else ''}."
        lines = []
        for m in conversations[:10]:
            arrow = "da" if m.incoming else "a"
            new = "🆕 " if m.incoming and not m.read else ""
            lines.append(f"{new}{m.date:%d/%m %H:%M} {arrow} {m.who}: {m.body[:200]}")
        return "SMS:\n" + "\n".join(lines)

    def send_sms(to: str, text: str) -> str:
        phone = kc.find()
        if phone is None:
            return "Il telefono non è vicino: non posso mandare SMS."
        found = msg.find_number(to, contacts())
        if found is None:
            return f"Non trovo un solo contatto «{to}» nella rubrica del telefono: dimmi il nome completo o il numero."
        number, name = found
        ok = kc.send_sms(phone, number, text)
        return f"SMS inviato a {name or number}." if ok else "Invio non riuscito."

    photos_state: dict[str, Any] = {}

    def photo_sync():
        from ..mesh.photos import PhotoSync

        if "sync" not in photos_state:
            photos_state["sync"] = PhotoSync()
        return photos_state["sync"]

    def sync_photos() -> str:
        from ..mesh.photos import describe, mount_phone

        phone = kc.find()
        if phone is None:
            return "Il telefono non è vicino: le foto le salvo appena torna sulla stessa rete del PC."
        root = mount_phone(bus, phone.id)
        if root is None:
            return ("Non riesco ad aprire la memoria del telefono: nell'app KDE Connect attiva «Sfoglia questo dispositivo» "
                    "(su iPhone usa «Invia foto al PC» nella pagina «Il mio PC»).")
        result = photo_sync().run(phone.id, root, deadline=time.monotonic() + 600)
        return describe(result, phone.name)

    def improve_photos(which: str = "ultime") -> str:
        from ..mesh.photos import enhance

        if which.strip().startswith(("/", "~")):
            targets = [Path(which.strip()).expanduser()]
        else:
            targets = photo_sync().last_batch()
        targets = [t for t in targets if t.is_file()][:50]
        if not targets:
            return "Non ho foto da migliorare: dimmi il percorso, o prima «sincronizza le foto»."
        done, method = [], ""
        for t in targets:
            out, method = enhance(t, which=runner.which)
            if out:
                done.append(out)
        if not done:
            return f"Non sono riuscito a migliorarle: {method}"
        return (f"Migliorate {len(done)} foto con {method}. Le originali restano; le nuove si chiamano «… (migliorata).jpg»"
                + (f", es. {done[0]}" if len(done) == 1 else "") + ".")

    def bluetooth_devices() -> str:
        from ..mesh.bluetooth import Bluetooth

        bt = Bluetooth(runner)
        if not bt.available():
            return "Su questo dispositivo non c'è il Bluetooth (bluetoothctl)."
        known, here = bt.remember_local(), set(bt.paired())
        if not known:
            return "Non hai ancora dispositivi Bluetooth abbinati."
        lines = ["I tuoi dispositivi Bluetooth (condivisi tra i tuoi dispositivi SoIA):"]
        for mac, info in known.items():
            state = "abbinato qui" if mac in here else f"abbinato a {info.get('da') or 'un altro dispositivo'}: lo collego qui appena è vicino"
            lines.append(f"  {info.get('nome', mac)} — {state}")
        return "\n".join(lines)

    def forget_bluetooth(name: str) -> str:
        from ..mesh.bluetooth import Bluetooth, forget

        hits = forget(Bluetooth(runner), name)
        return (f"Dimenticato «{name}»: scollegato qui e tolto dall'elenco di tutti i tuoi dispositivi." if hits
                else f"Non trovo un dispositivo Bluetooth «{name}».")

    def install_aios_phone() -> str:
        import sys

        runner.spawn([sys.executable, "-m", "aios_copilot.phoneapp"])
        return ("Apro l'installatore di SoIA per telefono. Collega il telefono con un cavo USB dati: ti guido io passo "
                "per passo. Prima di cancellare qualsiasi cosa faccio il backup completo sul PC, e alla fine lo rimetto "
                "sul telefono nuovo.")

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
        state = command({"azione": "stato"}) or {}
        if state.get("collegamento"):
            lines.append("Senza Wi-Fi: " + state["collegamento"])
        return "\n".join(lines)

    def link_phone(kind: str = "auto") -> str:
        reply = command({"azione": "vicino", "tipo": kind})
        if reply is None:
            return "Il servizio del telefono non è attivo (systemctl --user start aios-telefono)."
        return reply.get("testo") or reply.get("errore", "Non ci sono riuscito.")

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
        Tool("install_aios_phone", "Apre l'installatore guidato di SoIA per un telefono collegato via USB "
             "(Pixel, Samsung, Motorola, Xiaomi, Oppo), con backup e ripristino.", params(), install_aios_phone),
        Tool("bluetooth_devices", "Elenca i dispositivi Bluetooth dell'utente condivisi tra telefono e PC.", params(),
             bluetooth_devices),
        Tool("forget_bluetooth", "Dimentica un dispositivo Bluetooth su tutti i dispositivi dell'utente.",
             params(name="Nome del dispositivo"), forget_bluetooth, requires_confirmation=True),
        Tool("sync_photos", "Salva sul PC le foto e i video della fotocamera del telefono che mancano "
             "(non WhatsApp né screenshot).", params(), sync_photos),
        Tool("improve_photos", "Migliora le foto (le ultime salvate dal telefono, o un file): luce, rumore, nitidezza; "
             "con il modello AI se c'è.", params([], which="«ultime» o percorso della foto"), improve_photos),
        Tool("phone_notifications", "Riassume le notifiche del telefono: messaggi delle persone, codici, chiamate, altro.",
             params(), phone_notifications, reads_private=True),
        Tool("copy_code", "Copia sul PC l'ultimo codice di verifica (OTP) arrivato sul telefono.", params(), copy_code,
             reads_private=True),
        Tool("reply_message", "Risponde a un messaggio arrivato sul telefono (WhatsApp, Telegram, SMS…).",
             params(who="Chi ha scritto (nome) o app", text="Testo della risposta"), reply_message,
             requires_confirmation=True, sends_out=True),
        Tool("read_sms", "Legge gli SMS del telefono (gli ultimi, o quelli di una persona).",
             params([], who="Nome o numero"), read_sms, reads_private=True),
        Tool("send_sms", "Manda un SMS dal telefono a un contatto della rubrica o a un numero.",
             params(to="Nome o numero", text="Testo"), send_sms, requires_confirmation=True, sends_out=True),
        Tool("link_phone", "Collega PC e telefono vicini anche senza Wi-Fi (Bluetooth o Wi-Fi diretto, scelti da "
             "Nova), usa internet del telefono (hotspot), oppure chiude il collegamento.",
             params([], kind=("Collegamento", ["auto", "wifi", "bluetooth", "internet", "chiudi"])), link_phone),
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


RE_NOTIFS = re.compile(rf"^(?:(?:le\s+)?notifiche\s+del\s+telefono|cosa\s+c'è\s+(?:di\s+nuovo\s+)?sul\s+telefono|"
                       rf"novità\s+sul\s+telefono|ho\s+(?:nuovi\s+|dei\s+)?messaggi(?:\s+sul\s+telefono)?)\??$")
RE_CODE = re.compile(r"^(?:copia(?:mi)?|dammi|qual\s+è|che)\s+(?:il\s+)?codice(?:\s+(?:di\s+verifica|del\s+telefono|"
                     r"che\s+mi\s+è\s+arrivato|arrivato|sms))?\??$|^(?:che\s+)?codice\s+mi\s+è\s+arrivato\??$")
RE_SMS_READ = re.compile(r"^(?:leggi(?:mi)?\s+|mostra(?:mi)?\s+)?(?:gli\s+|i\s+miei\s+|ultimi\s+)?sms"
                         r"(?:\s+(?:di|da)\s+(?P<who>.+?))?\??$|^che\s+sms\s+ho\??$")
RE_SMS_SEND = re.compile(r"^(?:manda|invia|scrivi)\s+(?:un\s+)?sms\s+a\s+(?P<to>[^:]+?)\s*(?::|dicendo\s+(?:che\s+)?|con\s+scritto\s+)"
                         r"\s*(?P<text>.+)$", re.S)
RE_REPLY = re.compile(r"^rispondi\s+a\s+(?P<who>[^:]+?)(?:\s+su\s+\w+)?\s*:\s*(?P<text>.+)$", re.S)


def _original(pattern: re.Pattern[str], text: str, low_match: re.Match[str], group: str) -> str:
    """Il testo da inviare così come l'ha scritto l'utente (maiuscole, punteggiatura, «grazie» finale)."""
    m = re.compile(pattern.pattern, pattern.flags | re.I).match(" ".join(text.split()))
    return m.group(group).strip() if m else low_match.group(group).strip()


RE_LINK = re.compile(rf"^(?:collegati|connettiti)\s+(?:al\s+(?:mio\s+)?(?:telefono|cellulare)|{PHONE})"
                     r"(?:\s+(?:senza\s+wi-?fi|via\s+bluetooth|anche\s+senza\s+rete))?$")
RE_HOTSPOT = re.compile(r"^(?:usa|prendi|condividi|attiva)\s+(?:l'|la\s+)?(?:internet|connessione|rete|hotspot)\s+"
                        r"(?:del|dal)\s+(?:mio\s+)?(?:telefono|cellulare)$|^hotspot\s+(?:del\s+)?(?:telefono|cellulare)$")
RE_UNLINK = re.compile(r"^(?:smetti\s+di\s+usare|stacca|chiudi|spegni)\s+(?:l'|la\s+)?(?:internet|connessione|"
                       r"collegamento|hotspot)\s+(?:del|col|con\s+il)\s+(?:mio\s+)?(?:telefono|cellulare)$")
RE_INSTALL_PHONE = re.compile(r"^(?:installa|metti|porta)\s+(?:aios|soia)\s+(?:sul|nel)\s+(?:mio\s+)?(?:telefono|cellulare|smartphone)$")
RE_BT = re.compile(r"^(?:i\s+)?(?:miei\s+)?dispositivi\s+bluetooth$|^(?:quali|che)\s+dispositivi\s+bluetooth\s+ho\??$")
RE_BT_FORGET = re.compile(r"^(?:dimentica|scollega\s+ovunque)\s+(?:le\s+|il\s+|la\s+|lo\s+|gli\s+)?(?P<n>.+?)\s+(?:dal|del)\s+bluetooth$"
                          r"|^dimentica\s+il\s+dispositivo\s+bluetooth\s+(?P<m>.+)$")
RE_PHOTOS = re.compile(r"^(?:sincronizza|salva|copia|scarica|porta|backup\s+del)(?:mi)?\s+(?:le\s+|tutte\s+le\s+)?(?:mie\s+)?"
                       r"(?:foto|fotografie)(?:\s+e\s+(?:i\s+)?video)?(?:\s+del\s+telefono)?(?:\s+(?:sul|nel|al)\s+(?:pc|computer))?$")
RE_IMPROVE = re.compile(r"^migliora\s+(?:le\s+(?:ultime\s+)?foto|(?:questa|la)\s+foto)(?:\s+(?P<p>[/~]\S+))?$")


class PhoneRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text)
        if RE_HOTSPOT.match(low):
            return Intent("link_phone", {"kind": "internet"})
        if RE_UNLINK.match(low):
            return Intent("link_phone", {"kind": "chiudi"})
        if RE_LINK.match(low):
            return Intent("link_phone", {"kind": "bluetooth" if "bluetooth" in low else "auto"})
        if RE_INSTALL_PHONE.match(low):
            return Intent("install_aios_phone", {})
        if RE_PHOTOS.match(low):
            return Intent("sync_photos", {})
        if RE_BT.match(low):
            return Intent("bluetooth_devices", {})
        m = RE_BT_FORGET.match(low)
        if m:
            return Intent("forget_bluetooth", {"name": (m.group("n") or m.group("m")).strip()})
        m = RE_IMPROVE.match(low)
        if m:
            path = re.search(r"[/~]\S+", text)
            return Intent("improve_photos", {"which": path.group(0) if path else "ultime"})
        if RE_NOTIFS.match(low):
            return Intent("phone_notifications", {})
        if RE_CODE.match(low):
            return Intent("copy_code", {})
        m = RE_SMS_SEND.match(low)
        if m:
            return Intent("send_sms", {"to": m.group("to").strip(), "text": _original(RE_SMS_SEND, text, m, "text")})
        m = RE_SMS_READ.match(low)
        if m:
            return Intent("read_sms", {"who": (m.group("who") or "").strip()})
        m = RE_REPLY.match(low)
        if m:
            return Intent("reply_message", {"who": m.group("who").strip(), "text": _original(RE_REPLY, text, m, "text")})
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
