"""Posta: strumenti per il copilota e riconoscimento istantaneo delle frasi."""

from __future__ import annotations

import re
from typing import Callable

from ..fastpath import Intent, normalize
from ..mail.classify import CATEGORIES
from ..mail.store import MailStore
from ..privacy import redact_secrets
from .base import Tool, offer, params

CATEGORY_WORDS = {
    "newsletter": "newsletter", "pubblicità": "newsletter", "promozioni": "newsletter", "spam": "newsletter",
    "lavoro": "lavoro", "personali": "personali", "personale": "personali", "importanti": "importanti",
    "importante": "importanti", "notifiche": "notifiche", "ricevute": "ricevute", "abbonamenti": "ricevute",
}


def make_tools(get_store: Callable[[], MailStore], send: Callable[[list[str], str, str, int | None], str],
               has_accounts: Callable[[], bool]) -> list[Tool]:
    def mail_overview() -> str:
        if not has_accounts():
            offer("collega la posta")
            return "Non hai ancora collegato la posta. Vuoi farlo adesso? Ti apro la schermata giusta."
        store = get_store()
        counts = store.counts()
        unread = sum(u for _, u in counts.values())
        if not unread:
            return "Nessuna mail nuova. 📭"
        lines = [f"Hai {unread} mail non lette."]
        important = [m for m in store.inbox(50, unread=True) if m.importance >= 60][:5]
        if important:
            lines.append("Da guardare:")
            lines += [f"  {m.line()} ({m.reason})" for m in important]
        parts = [f"{CATEGORIES[c].split(' ', 1)[1]}: {u}" for c, (_, u) in counts.items() if u and c in CATEGORIES]
        if parts:
            lines.append("Per categoria — " + ", ".join(parts) + ".")
        return "\n".join(lines)

    def search_mail(query: str) -> str:
        found = get_store().search(query)
        if not found:
            return f"Nessuna mail trovata per «{query}»."
        return "\n".join(f"{m.line()}\n  {m.snippet}" for m in found)

    def read_mail(mail_id: str) -> str:
        if not str(mail_id).strip().isdigit():
            return "Indica il numero della mail (tra parentesi quadre)."
        m = get_store().get(int(mail_id))
        if m is None:
            return f"Non trovo la mail {mail_id}."
        attachments = f"\nAllegati: {', '.join(m.attachments)}" if m.attachments else ""
        # I segreti (codici, password) non vanno mai al modello.
        return (f"Da: {m.sender_name} <{m.sender}>\nData: {m.date:%d/%m/%Y %H:%M}\nOggetto: {m.subject}{attachments}\n\n"
                + redact_secrets(m.body[:6000]))

    def send_email(to: str, subject: str, body: str, reply_to: str = "") -> str:
        recipients = []
        for who in re.split(r"[,;]\s*", to):
            who = who.strip()
            if "@" in who:
                recipients.append(who)
                continue
            found = get_store().contacts(who)
            if not found:
                return f"Non conosco l'indirizzo di «{who}». Scrivimelo per intero."
            if len(found) > 1 and found[0][2] < 3 * found[1][2]:
                options = ", ".join(f"{n} <{a}>" for n, a, _ in found[:4])
                return f"Ci sono più persone che si chiamano «{who}»: {options}. A chi la mando?"
            recipients.append(found[0][1])
        reply = int(reply_to) if str(reply_to).isdigit() else None
        return send(recipients, subject, body, reply)

    def categorize_mail(who: str, category: str) -> str:
        cat = CATEGORY_WORDS.get(category.lower().strip(), category.lower().strip())
        if cat not in CATEGORIES:
            return f"Categorie possibili: {', '.join(CATEGORIES)}."
        store = get_store()
        pattern = who.strip().lower()
        if "@" not in pattern:
            found = store.contacts(pattern)
            if not found:
                return f"Non trovo mail di «{who}»."
            pattern = found[0][1]
        changed = store.set_override(pattern, cat)
        return f"D'accordo: le mail di {pattern} andranno in «{CATEGORIES[cat]}» ({changed} già spostate)."

    def connect_mail() -> str:
        import shutil
        import subprocess

        exe = shutil.which("aios-shell")
        if exe:
            try:
                subprocess.run([exe, "--vista", "impostazioni:posta"], timeout=10, capture_output=True)
                return "Ecco: scrivi il tuo indirizzo e la password (o accedi con il tuo account) e la collego."
            except (OSError, subprocess.SubprocessError):
                pass
        return "Apri Impostazioni › Posta: scrivi indirizzo e password e la collego."

    return [
        Tool("connect_mail", "Collega un account di posta (apre Impostazioni › Posta).", params(), connect_mail),
        Tool("mail_overview", "Riepilogo della posta: non lette, importanti, categorie.", params(), mail_overview,
             reads_private=True),
        Tool("search_mail", "Cerca nelle email dell'utente.", params(query="Parole da cercare"), search_mail,
             reads_private=True),
        Tool("read_mail", "Legge una email (numero tra parentesi quadre).", params(mail_id="Numero"), read_mail,
             reads_private=True),
        Tool("send_email", "Invia un'email. 'to' può essere un indirizzo o il nome di un contatto. "
             "'reply_to' è il numero della mail a cui rispondere.",
             params(["to", "subject", "body"], to="Destinatari", subject="Oggetto", body="Testo", reply_to="Numero mail"),
             send_email, requires_confirmation=True, sends_out=True),
        Tool("categorize_mail", "Sposta le mail di un mittente (nome, indirizzo o @dominio) in una categoria, anche in futuro.",
             params(who="Mittente", category=("Categoria", list(CATEGORIES))), categorize_mail),
    ]


RE_OVERVIEW = re.compile(
    r"^(?:ho (?:nuove |delle |altre )?(?:mail|email|e-mail|posta)|ci sono (?:nuove |delle )?(?:mail|email)|"
    r"controlla (?:la posta|le mail|le email)|leggi(?:mi)? (?:la posta|le mail|le email)|(?:le )?mie mail|"
    r"posta in arrivo|(?:novità|cosa c'è) nella posta|any new (?:mail|email)s?|check (?:my )?(?:mail|email))"
)
RE_SEARCH = re.compile(r"^(?:cerca|trova)\s+(?:nelle|tra le|nella)\s+(?:mie\s+)?(?:mail|email|posta)\s+(?P<x>.+)$")
RE_READ = re.compile(r"^(?:leggi|apri|mostra(?:mi)?)\s+(?:la\s+)?(?:mail|email)\s+(?:numero\s+)?(?P<n>\d+)$")
RE_CATEGORIZE = re.compile(
    r"^(?:le mail|i messaggi|le email)\s+(?:di|da)\s+(?P<who>.+?)\s+sono\s+(?:delle\s+|dei\s+)?(?P<cat>"
    + "|".join(CATEGORY_WORDS) + r")$")


RE_CONNECT = re.compile(r"^(?:collega(?:mi)?|aggiungi|configura|imposta)\s+(?:la\s+|le\s+|il\s+|un\s+|l')?"
                        r"(?:mia\s+|mie\s+|mio\s+)?(?:posta|mail|email|e-mail|casella(?:\s+di\s+posta)?|account\s+(?:di\s+posta|email))$")


class MailRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text)
        if RE_CONNECT.match(low.strip(" .!?")):
            return Intent("connect_mail", {})
        if RE_OVERVIEW.match(low):
            return Intent("mail_overview", {})
        m = RE_SEARCH.match(low)
        if m:
            return Intent("search_mail", {"query": m.group("x")})
        m = RE_READ.match(low)
        if m:
            return Intent("read_mail", {"mail_id": m.group("n")})
        m = RE_CATEGORIZE.match(low)
        if m:
            return Intent("categorize_mail", {"who": m.group("who"), "category": m.group("cat")})
        return None
