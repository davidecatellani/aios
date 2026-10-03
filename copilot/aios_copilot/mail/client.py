"""Collegamento ai server di posta (IMAP per leggere, SMTP per inviare).

La sincronizzazione scarica le mail nuove a piccoli lotti e salva il punto di
arrivo dopo ogni lotto: può fermarsi in qualsiasi momento e riprendere. Le mail
si leggono con BODY.PEEK, quindi sul server restano «non lette» finché non le
apri davvero.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import imaplib
import json
import os
import re
import smtplib
import ssl
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable

from .. import vault
from ..tools.web import html_to_text
from . import oauth
from .classify import FREE_MAIL, classify
from .providers import PROVIDERS, Provider, provider_for
from .store import MailStore

FIRST_SYNC_DAYS = 90
BATCH = 20
MAX_FETCH_BYTES = 512 * 1024  # per mail: testo sì, allegati enormi no


@dataclass
class Account:
    address: str
    provider: str = ""  # chiave in PROVIDERS, oppure "" per un server configurato a mano
    auth: str = "password"  # "password" | "oauth"
    imap_host: str = ""
    smtp_host: str = ""
    imap_port: int = 993
    smtp_port: int = 465
    name: str = ""
    smtp_tls: str = ""  # "ssl" | "starttls" | "" = in base alla porta (465 ssl, altrimenti starttls)
    archive_categories: list[str] = field(default_factory=list)  # archiviazione sul server: solo se scelta
    tls_context: Any = field(default=None, repr=False, compare=False)

    @property
    def p(self) -> Provider:
        if self.provider in PROVIDERS:
            return PROVIDERS[self.provider]
        return Provider("Personalizzato", self.imap_host, self.smtp_host, self.imap_port, self.smtp_port)

    def secret_key(self) -> str:
        return f"mail:{self.address}"


def accounts_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "mail.json"


def load_accounts() -> list[Account]:
    try:
        return [Account(**a) for a in json.loads(accounts_path().read_text())]
    except (OSError, ValueError, TypeError):
        return []


def save_accounts(accounts: list[Account]) -> None:
    path = accounts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [{k: v for k, v in asdict(a).items() if k != "tls_context"} for a in accounts]
    path.write_text(json.dumps(data, indent=2))  # niente password qui: stanno nel portachiavi


def new_account(address: str, auth: str | None = None, **manual: Any) -> Account:
    found = provider_for(address)
    if found and not manual:
        key, p = found
        return Account(address, key, auth or ("oauth" if key == "outlook" else "password"),
                       p.imap_host, p.smtp_host, p.imap_port, p.smtp_port)
    return Account(address, "", auth or "password", **manual)


# --- accesso -------------------------------------------------------------------------


def _token(account: Account) -> str:
    cfg = account.p.oauth
    if cfg is None:
        raise oauth.OAuthError("Questo provider non supporta l'accesso OAuth.")
    tokens = json.loads(vault.load(account.secret_key()) or "{}")
    fresh = oauth.refresh(cfg, tokens)
    if fresh is not tokens:
        vault.store(account.secret_key(), json.dumps(fresh))
    return fresh["access_token"]


def imap_connect(account: Account) -> imaplib.IMAP4:
    ctx = account.tls_context or ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(account.p.imap_host, account.p.imap_port, ssl_context=ctx, timeout=30)
    if account.auth == "oauth":
        auth = oauth.xoauth2(account.address, _token(account))
        conn.authenticate("XOAUTH2", lambda _: auth.encode())
    else:
        conn.login(account.address, vault.load(account.secret_key()) or "")
    return conn


def smtp_send(account: Account, msg: EmailMessage) -> None:
    ctx = account.tls_context or ssl.create_default_context()
    host, port = account.p.smtp_host, account.p.smtp_port
    implicit = (account.smtp_tls or ("ssl" if port == 465 else "starttls")) == "ssl"
    server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) if implicit else smtplib.SMTP(host, port, timeout=30)
    try:
        if not implicit:
            server.starttls(context=ctx)  # mai in chiaro
        if account.auth == "oauth":
            auth = oauth.xoauth2(account.address, _token(account))
            server.auth("XOAUTH2", lambda challenge=None: auth)
        else:
            server.login(account.address, vault.load(account.secret_key()) or "")
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            pass


# --- lettura delle mail --------------------------------------------------------------


def _addr(value: str) -> tuple[str, str]:
    name, address = email.utils.parseaddr(value or "")
    return name, address.lower()


def _date(value: str | None) -> datetime:
    try:
        d = email.utils.parsedate_to_datetime(value)
        return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
    except (TypeError, ValueError):
        return datetime.now().replace(microsecond=0)


def parse_message(raw: bytes) -> dict[str, Any]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender_name, sender = _addr(str(msg.get("From", "")))
    recipients = [a.lower() for _, a in email.utils.getaddresses(
        [str(msg.get(h, "")) for h in ("To", "Cc")]) if a]
    body = ""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is not None:
            content = part.get_content()
            body = html_to_text(content, 200_000) if part.get_content_type() == "text/html" else content
    except (KeyError, LookupError, ValueError):
        pass
    attachments = []
    try:
        attachments = [a.get_filename() or "allegato" for a in msg.iter_attachments()]
    except (KeyError, ValueError, AttributeError):
        pass
    body = re.sub(r"\n{3,}", "\n\n", body.replace("\r", "")).strip()
    headers = {k.lower(): str(v)[:200] for k, v in msg.items()
               if k.lower() in ("list-unsubscribe", "precedence", "auto-submitted", "reply-to")}
    return {
        "msgid": str(msg.get("Message-ID", "")).strip(), "in_reply_to": str(msg.get("In-Reply-To", "")).strip(),
        "sender_name": sender_name, "sender": sender, "recipients": recipients,
        "subject": str(msg.get("Subject", "")).strip(), "date": _date(msg.get("Date")),
        "snippet": re.sub(r"\s+", " ", body)[:160], "body": body[:100_000], "attachments": attachments,
        "headers": headers,
    }


def special_folders(conn: imaplib.IMAP4) -> dict[str, str]:
    """INBOX e la cartella della posta inviata (attributo \\Sent, o nomi comuni)."""
    folders = {"inbox": "INBOX"}
    typ, data = conn.list()
    for line in data or []:
        text = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        m = re.match(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', text)
        if not m:
            continue
        name = m.group("name").strip().strip('"')
        if "\\Sent" in m.group("flags") or name.lower() in ("sent", "inviata", "posta inviata", "sent items", "sent messages"):
            folders.setdefault("sent", name)
    return folders


class MailSync:
    """Sincronizzazione incrementale di un account, a lotti."""

    def __init__(self, account: Account, store: MailStore, connect: Callable[[Account], imaplib.IMAP4] = imap_connect,
                 known_service: Callable[[str], str | None] = lambda sender: None, clock: Callable[[], datetime] = datetime.now):
        self.account, self.store, self.connect = account, store, connect
        self.known_service, self.clock = known_service, clock
        self.conn: imaplib.IMAP4 | None = None
        self._queue: list[tuple[str, int, list[int]]] = []  # (cartella, uidvalidity, uid da scaricare)
        self.new_ids: list[int] = []

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
            self.conn = None

    def plan(self) -> int:
        """Elenca le mail nuove di ogni cartella; restituisce quante sono."""
        self.conn = self.conn or self.connect(self.account)
        self._queue = []
        folders = special_folders(self.conn)
        # Prima la posta inviata: per classificare quella in arrivo serve sapere a chi scrivi.
        for folder in [folders[k] for k in ("sent", "inbox") if k in folders]:
            typ, _ = self.conn.select(f'"{folder}"', readonly=True)
            if typ != "OK":
                continue
            uidvalidity = int((self.conn.response("UIDVALIDITY")[1] or [b"0"])[0])
            known_validity, last_uid = self.store.state(self.account.address, folder)
            if known_validity and known_validity != uidvalidity:  # la cartella è stata ricreata sul server
                self.store.reset_folder(self.account.address, folder)
                last_uid = 0
            if last_uid:
                typ, data = self.conn.uid("SEARCH", None, f"UID {last_uid + 1}:*")
            else:
                since = (self.clock() - timedelta(days=FIRST_SYNC_DAYS)).strftime("%d-%b-%Y")
                typ, data = self.conn.uid("SEARCH", None, f"SINCE {since}")
            uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > last_uid)
            if uids:
                self._queue.append((folder, uidvalidity, uids))
            elif not known_validity:
                self.store.set_state(self.account.address, folder, uidvalidity, last_uid)
        return sum(len(u) for _, _, u in self._queue)

    def has_work(self) -> bool:
        return bool(self._queue)

    def _own_domain(self) -> str:
        """Il dominio dell'utente, se è aziendale: chi scrive da lì è un collega."""
        domain = self.account.address.rsplit("@", 1)[-1].lower()
        return "" if domain in FREE_MAIL or provider_for(self.account.address) else domain

    def step(self) -> int:
        """Scarica un lotto; restituisce quante mail nuove sono state salvate."""
        if not self._queue:
            return 0
        folder, uidvalidity, uids = self._queue[0]
        batch, rest = uids[:BATCH], uids[BATCH:]
        self.conn.select(f'"{folder}"', readonly=True)
        typ, data = self.conn.uid("FETCH", ",".join(map(str, batch)), f"(UID FLAGS BODY.PEEK[]<0.{MAX_FETCH_BYTES}>)")
        saved = 0
        for item in data or []:
            if not isinstance(item, tuple):
                continue
            meta = item[0].decode(errors="replace")
            m = re.search(r"UID (\d+)", meta)
            if not m:
                continue
            fields = parse_message(item[1])
            sender = fields["sender"]
            verdict = classify(sender, fields["subject"], fields["body"], fields["headers"],
                               sent_to_count=self.store.sent_count(sender),
                               own_domain=self._own_domain(),
                               known_service=self.known_service(sender), override=self.store.override_for(sender))
            is_sent = folder != "INBOX"
            mid = self.store.add({**fields, "account": self.account.address, "folder": folder, "uidvalidity": uidvalidity,
                                  "uid": int(m.group(1)), "seen": int(is_sent or "\\Seen" in meta),
                                  "category": "inviate" if is_sent else verdict.category,
                                  "importance": 0 if is_sent else verdict.importance, "reason": verdict.reason})
            if mid:
                saved += 1
                self.new_ids.append(mid)
        self.store.set_state(self.account.address, folder, uidvalidity, batch[-1])  # checkpoint
        if rest:
            self._queue[0] = (folder, uidvalidity, rest)
        else:
            self._queue.pop(0)
        return saved

    # --- azioni sul server --------------------------------------------------------
    def mark_seen(self, mail_id: int) -> None:
        mail = self.store.get(mail_id)
        if mail is None:
            return
        self.conn = self.conn or self.connect(self.account)
        self.conn.select(f'"{mail.folder}"')
        self.conn.uid("STORE", str(mail.uid), "+FLAGS", "(\\Seen)")
        self.store.mark_seen(mail_id)

    def archive(self, mail_id: int, category_label: str) -> bool:
        """Sposta (mai cancella) in «AIOS/<categoria>»; reversibile dal client."""
        mail = self.store.get(mail_id)
        if mail is None:
            return False
        self.conn = self.conn or self.connect(self.account)
        target = f"AIOS/{category_label}"
        self.conn.create(f'"{target}"')  # se esiste già il server risponde NO: va bene
        self.conn.select(f'"{mail.folder}"')
        caps = {c.upper() for c in self.conn.capabilities}
        uid = str(mail.uid)
        if "MOVE" in caps:
            typ, _ = self.conn.uid("MOVE", uid, f'"{target}"')
        elif "UIDPLUS" in caps:
            # Mai EXPUNGE semplice: cancellerebbe anche le mail segnate da altri programmi.
            typ, _ = self.conn.uid("COPY", uid, f'"{target}"')
            if typ == "OK":
                self.conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                typ, _ = self.conn._simple_command("UID", "EXPUNGE", uid)  # solo questa mail
        else:
            return False  # senza MOVE né UIDPLUS non si può spostare in sicurezza
        if typ != "OK":
            return False
        self.store.set_archived(mail_id, target)
        return True


def build_message(account: Account, to: list[str], subject: str, body: str, reply_to: Any = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr((account.name, account.address)) if account.name else account.address
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=account.address.split("@")[1])
    if reply_to is not None and getattr(reply_to, "msgid", ""):
        msg["In-Reply-To"] = reply_to.msgid
        msg["References"] = reply_to.msgid
    msg.set_content(body)
    return msg


def save_to_sent(account: Account, conn: imaplib.IMAP4, msg: EmailMessage) -> None:
    """I server che non salvano da soli la posta inviata: la si aggiunge a mano."""
    if account.p.saves_sent:
        return
    sent = special_folders(conn).get("sent")
    if sent:
        conn.append(f'"{sent}"', "(\\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
