"""Identità dell'utente: creare, recuperare, dispositivi, revoche, sincronizzazione."""

from __future__ import annotations

import re
from typing import Any, Callable

from .. import identity as ident
from ..fastpath import Intent, normalize
from ..mesh.service import send_command
from .base import Tool, params

KINDS = {"pc": "💻", "telefono": "📱", "tablet": "📲"}


def format_phrase(words: list[str]) -> str:
    rows = [" ".join(f"{i + 1:>2}. {w:<11}" for i, w in enumerate(words) if i // 6 == r) for r in range(3)]
    return "\n".join(r.rstrip() for r in rows)


def make_tools(command: Callable[[dict[str, Any]], dict[str, Any] | None] = send_command,
               user_name: Callable[[], str] = lambda: "") -> list[Tool]:
    def identity_status() -> str:
        me = ident.Identity.load()
        if me is None:
            return ("Non hai ancora un'identità SoIA. Con «crea la mia identità» i tuoi dispositivi si riconoscono "
                    "tra loro e si sincronizzano (agenda, nome, temi) in modo cifrato.")
        this = me.certificate.id
        lines = [f"Identità di {me.name or 'utente'}. Dispositivi:"]
        for c in me.devices:
            state = " (revocato)" if c.id in me.revoked else " ← questo" if c.id == this else ""
            lines.append(f"  {KINDS.get(c.kind, '•')} {c.name}{state}")
        if me.data.get("relay"):
            lines.append(f"Sincronizzazione fuori casa: relay {me.data['relay']['url']} (dati cifrati)")
        peers = me.data.get("pari", [])
        if peers:
            lines.append("Si sincronizza con: " + ", ".join(p.get("nome", p["url"]) for p in peers))
        lines.append("Questo dispositivo custodisce la chiave principale." if me.master() else
                     "La chiave principale è su un altro dispositivo (e nella tua frase di recupero).")
        return "\n".join(lines)

    def create_identity(name: str = "") -> str:
        if ident.Identity.load() is not None:
            return "Hai già un'identità SoIA su questo dispositivo: «la mia identità» per vederla."
        me, words = ident.create(name or user_name())
        return ("Fatto: ho creato la tua identità SoIA. Questa è la tua frase di recupero: scrivila su carta e "
                "conservala in un posto sicuro. Serve per ritrovare la tua identità se perdi tutti i dispositivi; "
                "chi la conosce può prendere il tuo posto, quindi non fotografarla e non darla a nessuno.\n\n"
                f"{format_phrase(words)}\n\n"
                "Ora collega gli altri dispositivi con «collega il telefono»: riceveranno un certificato e si "
                "sincronizzeranno da soli.")

    def show_recovery_phrase() -> str:
        words = ident.recovery_phrase()
        if words is None:
            return "La frase di recupero non è custodita su questo dispositivo."
        return "La tua frase di recupero (non condividerla con nessuno):\n\n" + format_phrase(words)

    def restore_identity(phrase: str) -> str:
        if ident.Identity.load() is not None:
            return "Su questo dispositivo c'è già un'identità."
        try:
            me = ident.restore(phrase, name=user_name())
        except ident.IdentityError as exc:
            return str(exc)
        return (f"Identità ritrovata. Questo dispositivo ({me.certificate.name}) ora custodisce la chiave principale. "
                "Collega gli altri dispositivi con «collega il telefono» per ritrovare agenda, nome e temi.")

    def revoke_device(name: str) -> str:
        me = ident.Identity.load()
        if me is None:
            return "Non c'è un'identità SoIA su questo dispositivo."
        try:
            gone = me.revoke(name)
        except ident.IdentityError as exc:
            return str(exc)
        if not gone:
            return f"Non trovo un dispositivo «{name}» (questo non si può revocare da sé)."
        return (f"Revocato: {', '.join(c.name for c in gone)}. Non potrà più sincronizzarsi né farsi riconoscere, e ho "
                "cambiato la chiave di sincronizzazione: anche se avesse in mano i dati nuovi non saprebbe leggerli. "
                "Gli altri dispositivi ricevono la nuova chiave alla prossima sincronizzazione.")

    def set_relay(url: str, fingerprint: str = "") -> str:
        me = ident.Identity.load()
        if me is None:
            return "Prima serve un'identità SoIA («crea la mia identità»)."
        url = url.strip().rstrip("/")
        if url in ("", "no", "nessuno", "spento"):
            me.data.pop("relay", None)
            me.save()
            return "Relay disattivato: i dispositivi si sincronizzano solo quando sono nella stessa rete."
        if not url.startswith("https://"):
            return "L'indirizzo del relay deve iniziare con https://"
        me.data["relay"] = {"url": url, "fingerprint": fingerprint.strip().lower()}
        me.save()
        return (f"Relay attivo: {url}. Ora i tuoi dispositivi si sincronizzano anche lontano da casa. Il relay "
                "conserva solo dati cifrati che non può leggere; lo saprà anche il resto dei tuoi dispositivi.")

    def sync_now() -> str:
        if ident.Identity.load() is None:
            return "Prima serve un'identità SoIA («crea la mia identità»)."
        reply = command({"azione": "sincronizza"})
        if reply is None:
            return "Il servizio aios-telefono non è attivo: systemctl --user enable --now aios-telefono"
        return "Sincronizzazione:\n" + ("\n".join(reply.get("righe", [])) or "nessun altro dispositivo da raggiungere")

    return [
        Tool("identity_status", "Mostra l'identità SoIA dell'utente e i suoi dispositivi.", params(), identity_status),
        Tool("create_identity", "Crea l'identità SoIA dell'utente (chiave principale e frase di recupero).",
             params([], name="Nome dell'utente"), create_identity, requires_confirmation=True),
        Tool("show_recovery_phrase", "Mostra la frase di recupero dell'identità.", params(), show_recovery_phrase,
             requires_confirmation=True, reads_private=True),
        Tool("restore_identity", "Ritrova l'identità SoIA su questo dispositivo con la frase di recupero (17 parole).",
             params(phrase="Frase di recupero"), restore_identity, requires_confirmation=True),
        Tool("revoke_device", "Revoca un dispositivo (perso, rubato, venduto): non sarà più riconosciuto.",
             params(name="Nome del dispositivo"), revoke_device, requires_confirmation=True),
        Tool("set_relay", "Attiva (indirizzo https) o disattiva («no») il relay cifrato per sincronizzare i dispositivi "
             "anche quando non sono nella stessa rete.", params(["url"], url="Indirizzo del relay o «no»",
                                                                   fingerprint="Impronta del certificato (facoltativa)"),
             set_relay, requires_confirmation=True),
        Tool("sync_now", "Sincronizza subito agenda, nome e temi con gli altri dispositivi.", params(), sync_now),
    ]


RE_CREATE = re.compile(r"^(?:crea|creami|attiva)\s+(?:la\s+mia\s+|una\s+)?(?:identità|account)(?:\s+(?:aios|soia))?$|^crea\s+il\s+mio\s+account(?:\s+(?:aios|soia))?$")
RE_STATUS = re.compile(r"^(?:la\s+mia\s+identità|il\s+mio\s+account|(?:i\s+)?dispositivi\s+del\s+mio\s+account)(?:\s+(?:aios|soia))?\??$")
RE_PHRASE = re.compile(r"^(?:mostra(?:mi)?|dammi|qual\s+è|rivedi)\s+(?:la\s+(?:mia\s+)?)?frase\s+di\s+recupero\??$")
RE_RESTORE = re.compile(r"^(?:ripristina|recupera|ritrova)\s+(?:la\s+mia\s+identità|il\s+mio\s+account)\s*:?\s*(?P<p>.*)$")
RE_REVOKE = re.compile(r"^(?:revoca|scollega\s+dal\s+mio\s+account|rimuovi\s+dal\s+mio\s+account)\s+(?:il\s+|la\s+|lo\s+)?"
                       r"(?:dispositivo\s+)?(?P<n>.+)$")
RE_RELAY = re.compile(r"^(?:usa|attiva|imposta)\s+(?:il\s+)?relay\s+(?P<url>https://\S+)$|^(?:disattiva|spegni)\s+(?:il\s+)?relay$")
RE_SYNC = re.compile(r"^sincronizza(?:\s+(?:i\s+(?:miei\s+)?dispositivi|tutto|ora|adesso))?$")


class IdentityRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text)
        if RE_CREATE.match(low):
            return Intent("create_identity", {})
        if RE_STATUS.match(low):
            return Intent("identity_status", {})
        if RE_PHRASE.match(low):
            return Intent("show_recovery_phrase", {})
        m = RE_RESTORE.match(low)
        if m and len(m.group("p").split()) == 17:
            return Intent("restore_identity", {"phrase": m.group("p")})
        m = RE_REVOKE.match(low)
        if m:
            return Intent("revoke_device", {"name": m.group("n").strip()})
        m = RE_RELAY.match(low)
        if m:
            url = re.search(r"https://\S+", text)
            return Intent("set_relay", {"url": url.group(0) if url else "no"})
        if RE_SYNC.match(low):
            return Intent("sync_now", {})
        return None
