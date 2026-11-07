"""Comando aios-mail e servizio di posta in background.

    aios-mail aggiungi tuo@gmail.com     collega un account (password per app o accesso OAuth)
    aios-mail sincronizza                scarica le mail nuove
    aios-mail stato                      account, categorie, non lette
    aios-mail archivia newsletter        archiviazione automatica (sposta, mai cancella) di una categoria
    aios-mail                            servizio: ogni 5 minuti sincronizza, notifica le importanti,
                                         archivia, aggiorna abbonamenti e rinnovi
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from datetime import datetime, timedelta
from typing import Callable

from .. import vault
from ..subscriptions import Subscriptions, renewal_suggestions, scan_mail, service_for_sender
from . import client, oauth
from .classify import CATEGORIES, NOTIFY_THRESHOLD
from .store import MailStore

SYNC_EVERY = 300
ARCHIVE_AFTER = timedelta(days=7)  # si archivia solo posta già letta e vecchia di una settimana


def known_service(sender: str) -> str | None:
    s = service_for_sender(sender)
    return s.name if s else None


def sync_all(store: MailStore, accounts: list[client.Account] | None = None,
             connect: Callable = client.imap_connect) -> tuple[list[int], list[str]]:
    """Sincronizza tutti gli account; restituisce (id nuovi, errori leggibili)."""
    new_ids, errors = [], []
    for account in accounts if accounts is not None else client.load_accounts():
        sync = client.MailSync(account, store, connect=connect, known_service=known_service)
        try:
            sync.plan()
            while sync.has_work():
                sync.step()
        except (OSError, oauth.OAuthError, Exception) as exc:  # un account in errore non blocca gli altri
            errors.append(f"{account.address}: {exc}")
        finally:
            new_ids += sync.new_ids
            sync.close()
    return new_ids, errors


def notify_important(store: MailStore, notify: Callable[[str, str], None]) -> int:
    mails = store.to_notify(NOTIFY_THRESHOLD)
    for m in mails[:5]:  # mai una raffica
        notify(f"✉️ {m.sender_name or m.sender}", f"{m.subject}\n{m.reason}")
    if len(mails) > 5:
        notify("✉️ Posta", f"Altre {len(mails) - 5} mail importanti")
    store.mark_notified(m.id for m in mails)
    return len(mails)


def auto_archive(store: MailStore, accounts: list[client.Account], connect: Callable = client.imap_connect,
                 now: datetime | None = None) -> int:
    moved = 0
    cutoff = (now or datetime.now()) - ARCHIVE_AFTER
    for account in accounts:
        if not account.archive_categories:
            continue
        sync = client.MailSync(account, store, connect=connect)
        try:
            for m in store.to_archive(account.archive_categories, cutoff):
                if m.account == account.address and sync.archive(m.id, CATEGORIES[m.category].split(" ", 1)[1]):
                    moved += 1
        finally:
            sync.close()
    return moved


def send_with_account(store: MailStore, to: list[str], subject: str, body: str, reply_to: int | None,
                      smtp: Callable = client.smtp_send, connect: Callable = client.imap_connect) -> str:
    accounts = client.load_accounts()
    if not accounts:
        return "Non hai ancora collegato un account di posta."
    original = store.get(reply_to) if reply_to else None
    account = next((a for a in accounts if original and a.address == original.account), accounts[0])
    if original and not subject.lower().startswith("re:"):
        subject = f"Re: {original.subject}"
    msg = client.build_message(account, to, subject, body, original)
    smtp(account, msg)
    try:
        conn = connect(account)
        client.save_to_sent(account, conn, msg)
        conn.logout()
    except Exception:
        pass  # la mail è partita; la copia in «Inviata» è un di più
    return f"Inviata a {', '.join(to)}: «{subject}». ✉️"


def run_service(store: MailStore, notify: Callable[[str, str], None], once: bool = False,
                sleep: Callable[[float], None] = time.sleep) -> None:
    from ..agenda import Agenda

    while True:
        accounts = client.load_accounts()
        new_ids, errors = sync_all(store, accounts)
        if new_ids:
            subs = Subscriptions()
            if scan_mail(subs, (store.get(i) for i in new_ids)):
                renewal_suggestions(subs, Agenda())
        notify_important(store, notify)
        auto_archive(store, accounts)
        for e in errors:
            print(f"posta: {e}", file=sys.stderr, flush=True)
        if once:
            return
        sleep(SYNC_EVERY)


def add_account(address: str, use_oauth: bool = False, ask: Callable[[str], str] = getpass.getpass) -> str:
    account = client.new_account(address, "oauth" if use_oauth else None)
    if account.auth == "oauth" and account.p.oauth:
        tokens = oauth.authorize(account.p.oauth, login_hint=address)
        vault.store(account.secret_key(), json.dumps(tokens))
    else:
        if account.p.password_help:
            print(account.p.password_help)
        vault.store(account.secret_key(), ask(f"Password per {address}: "))
    accounts = [a for a in client.load_accounts() if a.address != address] + [account]
    client.save_accounts(accounts)
    return f"Account {address} collegato ({account.p.name})."


def main(argv: list[str] | None = None) -> int:
    from ..agenda import notify

    parser = argparse.ArgumentParser(prog="aios-mail", description="Posta di SoIA")
    parser.add_argument("comando", nargs="?", default="servizio",
                        choices=["servizio", "aggiungi", "sincronizza", "stato", "archivia", "non-archiviare"])
    parser.add_argument("valore", nargs="?")
    parser.add_argument("--oauth", action="store_true", help="accesso con Google/Microsoft invece della password")
    args = parser.parse_args(argv)
    store = MailStore()
    if args.comando == "aggiungi":
        if not args.valore:
            print("Indica l'indirizzo: aios-mail aggiungi tuo@gmail.com")
            return 1
        try:
            print(add_account(args.valore, args.oauth))
        except oauth.OAuthError as exc:
            print(f"Accesso non riuscito: {exc}")
            return 1
    elif args.comando == "sincronizza":
        new_ids, errors = sync_all(store)
        print(f"{len(new_ids)} mail nuove." + "".join(f"\n⚠ {e}" for e in errors))
    elif args.comando == "stato":
        accounts = client.load_accounts()
        print("Account: " + (", ".join(a.address for a in accounts) or "nessuno"))
        for cat, (total, unread) in sorted(store.counts().items()):
            print(f"  {CATEGORIES.get(cat, cat)}: {total} ({unread} non lette)")
    elif args.comando in ("archivia", "non-archiviare"):
        if args.valore not in CATEGORIES:
            print(f"Categorie: {', '.join(CATEGORIES)}")
            return 1
        accounts = client.load_accounts()
        for a in accounts:
            cats = set(a.archive_categories)
            cats.add(args.valore) if args.comando == "archivia" else cats.discard(args.valore)
            a.archive_categories = sorted(cats)
        client.save_accounts(accounts)
        print(f"{CATEGORIES[args.valore]}: archiviazione automatica {'attiva' if args.comando == 'archivia' else 'disattivata'}.")
    else:
        try:
            run_service(store, notify)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
