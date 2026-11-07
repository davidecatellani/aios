"""Posta di SoIA: il client email nativo, con il copilota integrato.

    aios-posta            apre la posta (finestra dedicata o browser)

Le azioni avviate dall'utente con un clic (Invia, Archivia, Sposta in…) sono già
una conferma; quelle chieste al copilota passano dalle conferme del pannello.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..agenda import Agenda, find_deadlines
from ..localapp import LocalApp, open_window, serve
from ..when import describe
from . import client, service
from .classify import CATEGORIES
from .store import Mail, MailStore

PAGE = Path(__file__).with_name("ui.html")


def mail_json(m: Mail, full: bool = False) -> dict[str, Any]:
    data = {"id": m.id, "account": m.account, "sender_name": m.sender_name, "sender": m.sender, "subject": m.subject,
            "date": m.date.isoformat(), "snippet": m.snippet, "seen": m.seen, "category": m.category,
            "importance": m.importance, "reason": m.reason, "attachments": m.attachments}
    if full:
        data |= {"body": m.body, "recipients": m.recipients}
    return data


class MailApp(LocalApp):
    page = PAGE

    def __init__(self, store: MailStore, make_agent: Callable | None = None, agenda: Agenda | None = None,
                 accounts: Callable[[], list[client.Account]] = client.load_accounts,
                 send: Callable[..., str] | None = None, connect: Callable = client.imap_connect,
                 clock: Callable[[], datetime] = datetime.now):
        super().__init__(make_agent)
        self.store, self.agenda, self.accounts, self.connect, self.clock = store, agenda, accounts, connect, clock
        self.send = send or (lambda to, subject, body, reply: service.send_with_account(store, to, subject, body, reply,
                                                                                        connect=connect))
        r = self.route
        r("GET", r"/api/folders", self._folders)
        r("GET", r"/api/list", self._list)
        r("GET", r"/api/mail/(\d+)", self._mail)
        r("POST", r"/api/mail/(\d+)/seen", self._seen)
        r("POST", r"/api/mail/(\d+)/category", self._category)
        r("POST", r"/api/mail/(\d+)/archive", self._archive)
        r("POST", r"/api/send", self._send_mail)
        r("POST", r"/api/agenda", self._to_agenda)
        r("POST", r"/api/sync", self._sync)

    def prompt_for(self, body: dict[str, Any]) -> tuple[str, str | None]:
        text = body["text"].strip()
        mail = self.store.get(body["mail_id"]) if isinstance(body.get("mail_id"), int) else None
        if mail is None:
            return text, None
        # «Ricordamelo», «questa mail»: si sostituisce con l'oggetto, così anche i livelli veloci capiscono.
        subject = mail.subject
        text = re.sub(r"^ricordamelo\b", f"ricordami {subject}", text, flags=re.I)
        text = re.sub(r"\bquesta mail\b", subject, text, flags=re.I)
        return text, f"l'utente sta leggendo la mail [{mail.id}] di {mail.sender_name or mail.sender}: «{mail.subject}»"

    def _account(self, address: str) -> client.Account | None:
        return next((a for a in self.accounts() if a.address == address), None)

    # --- lettura ------------------------------------------------------------------
    def _folders(self, m, body, query):
        counts = self.store.counts()
        folders = [{"key": "tutte", "label": "📬 Tutte", "total": sum(t for t, _ in counts.values()),
                    "unread": sum(u for _, u in counts.values())}]
        folders += [{"key": k, "label": label, "total": counts.get(k, (0, 0))[0], "unread": counts.get(k, (0, 0))[1]}
                    for k, label in CATEGORIES.items()]
        important = [mail_json(x) for x in self.store.inbox(30, unread=True) if x.importance >= 60][:3]
        return 200, {"folders": folders, "accounts": [a.address for a in self.accounts()], "important": important,
                     "unread": sum(u for _, u in counts.values())}

    def _list(self, m, body, query):
        q = query.get("q", "").strip()
        if q:
            mails = self.store.search(q, 50)
        else:
            category = query.get("category")
            mails = self.store.inbox(100, category=None if category in (None, "", "tutte") else category)
        return 200, {"mails": [mail_json(x) for x in mails]}

    def _mail(self, m, body, query):
        mail = self.store.get(int(m.group(1)))
        if mail is None:
            return 404, {"error": "non trovata"}
        now = self.clock()
        dates = [{"title": t, "when": d.isoformat(), "label": describe(d, d.hour == 9 and d.minute == 0, now)}
                 for t, d in find_deadlines(f"{mail.subject}.\n{mail.body}", now)][:3]
        return 200, {"mail": mail_json(mail, full=True), "dates": dates,
                     "categories": [{"key": k, "label": v} for k, v in CATEGORIES.items()]}

    # --- azioni dell'utente ---------------------------------------------------------
    def _seen(self, m, body, query):
        mail = self.store.get(int(m.group(1)))
        if mail is None:
            return 404, {"error": "non trovata"}
        self.store.mark_seen(mail.id)
        account = self._account(mail.account)
        if account is not None:
            sync = client.MailSync(account, self.store, connect=self.connect)
            try:
                sync.mark_seen(mail.id)
            except Exception:
                pass  # resta letta in locale; il server si allinea alla prossima occasione
            finally:
                sync.close()
        return 200, {"ok": True}

    def _category(self, m, body, query):
        mail = self.store.get(int(m.group(1)))
        cat = body.get("category")
        if mail is None or cat not in CATEGORIES:
            return 400, {"error": "richiesta non valida"}
        pattern = mail.sender if body.get("scope") != "domain" else "@" + mail.sender.rsplit("@", 1)[-1]
        changed = self.store.set_override(pattern, cat)
        return 200, {"ok": True, "changed": changed,
                     "message": f"Da ora le mail di {pattern} vanno in «{CATEGORIES[cat]}»."}

    def _archive(self, m, body, query):
        mail = self.store.get(int(m.group(1)))
        account = self._account(mail.account) if mail else None
        if mail is None or account is None:
            return 404, {"error": "non trovata"}
        sync = client.MailSync(account, self.store, connect=self.connect)
        try:
            ok = sync.archive(mail.id, CATEGORIES[mail.category].split(" ", 1)[1])
        finally:
            sync.close()
        return 200, {"ok": ok, "message": "Archiviata (spostata, non cancellata)." if ok
                     else "Il server non permette di spostare la mail in sicurezza: lasciata dov'è."}

    def _send_mail(self, m, body, query):
        to = [x.strip() for x in str(body.get("to", "")).replace(";", ",").split(",") if "@" in x]
        if not to or not str(body.get("body", "")).strip():
            return 400, {"error": "Servono almeno un indirizzo e un testo."}
        reply = body.get("reply_to") if isinstance(body.get("reply_to"), int) else None
        return 200, {"message": self.send(to, str(body.get("subject", "")), str(body["body"]), reply)}

    def _to_agenda(self, m, body, query):
        try:
            when = datetime.fromisoformat(str(body.get("when")))
        except ValueError:
            return 400, {"error": "data non valida"}
        title = str(body.get("title", "")).strip()[:120] or "Dalla posta"
        agenda = self.agenda or Agenda()
        agenda.add_reminder(title, when)
        return 200, {"message": f"In agenda: «{title}» {describe(when, False, self.clock())}."}

    def _sync(self, m, body, query):
        new_ids, errors = service.sync_all(self.store, self.accounts(), connect=self.connect)
        return 200, {"new": len(new_ids), "errors": errors}


def main(argv: list[str] | None = None) -> int:
    from ..__main__ import make_agent

    store = MailStore()
    app = MailApp(store, make_agent)
    server, url = serve(app)
    try:
        if "--no-window" in (argv or sys.argv[1:]):
            print(url, flush=True)
            app.finished.wait()
        else:
            open_window(url, app.finished, "Posta", "org.aios.Mail", (1280, 820))
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
