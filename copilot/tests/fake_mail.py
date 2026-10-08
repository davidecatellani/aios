"""Server IMAP e SMTP minimi, con TLS, per provare il client con imaplib/smtplib veri."""

from __future__ import annotations

import base64
import re
import socketserver
import ssl
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path


def make_cert(directory: Path) -> tuple[Path, Path]:
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
                    "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                   check=True, capture_output=True)
    return cert, key


@dataclass
class Mailbox:
    users: dict[str, str] = field(default_factory=dict)  # utente -> password o token
    folders: dict[str, list[dict]] = field(default_factory=lambda: {"INBOX": [], "Sent": []})
    uidvalidity: dict[str, int] = field(default_factory=lambda: {"INBOX": 7, "Sent": 9})
    next_uid: dict[str, int] = field(default_factory=lambda: {"INBOX": 1, "Sent": 1})
    capabilities: str = "IMAP4rev1 AUTH=XOAUTH2 MOVE UIDPLUS"
    sent: list[tuple[str, list[str], bytes]] = field(default_factory=list)  # SMTP: (mittente, destinatari, dati)
    log: list[str] = field(default_factory=list)

    def deliver(self, raw: bytes, folder: str = "INBOX", seen: bool = False) -> int:
        self.folders.setdefault(folder, [])
        self.next_uid.setdefault(folder, 1)
        uid = self.next_uid[folder]
        self.next_uid[folder] += 1
        self.folders[folder].append({"uid": uid, "flags": {"\\Seen"} if seen else set(), "raw": raw})
        return uid


class _TLSServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, handler, mailbox: Mailbox, cert: Path, key: Path):
        super().__init__(("127.0.0.1", 0), handler)
        self.mailbox = mailbox
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)

    def get_request(self):
        sock, addr = super().get_request()
        return self.ctx.wrap_socket(sock, server_side=True), addr


class IMAPHandler(socketserver.StreamRequestHandler):
    def send(self, line: str | bytes) -> None:
        self.wfile.write((line.encode() if isinstance(line, str) else line) + b"\r\n")

    def handle(self) -> None:
        mb: Mailbox = self.server.mailbox
        selected = None
        self.send("* OK fake IMAP pronto")
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode().rstrip("\r\n")
            tag, _, rest = line.partition(" ")
            cmd, _, args = rest.partition(" ")
            cmd = cmd.upper()
            mb.log.append(f"{cmd} {args}")
            if cmd == "CAPABILITY":
                self.send(f"* CAPABILITY {mb.capabilities}")
                self.send(f"{tag} OK fatto")
            elif cmd == "LOGIN":
                user, pw = [a.strip('"') for a in args.split(" ", 1)]
                self.send(f"{tag} OK fatto" if mb.users.get(user) == pw else f"{tag} NO credenziali errate")
            elif cmd == "AUTHENTICATE":
                self.send("+ ")
                decoded = base64.b64decode(self.rfile.readline().strip()).decode()
                m = re.match(r"user=(.*)\x01auth=Bearer (.*)\x01\x01", decoded)
                ok = m and mb.users.get(m.group(1)) == m.group(2)
                self.send(f"{tag} OK fatto" if ok else f"{tag} NO token errato")
            elif cmd == "LIST":
                self.send('* LIST (\\HasNoChildren) "/" "INBOX"')
                for name in mb.folders:
                    if name != "INBOX":
                        flags = "\\HasNoChildren \\Sent" if name == "Sent" else "\\HasNoChildren"
                        self.send(f'* LIST ({flags}) "/" "{name}"')
                self.send(f"{tag} OK fatto")
            elif cmd in ("SELECT", "EXAMINE"):
                selected = args.strip('"')
                if selected not in mb.folders:
                    self.send(f"{tag} NO cartella inesistente")
                    continue
                self.send(f"* {len(mb.folders[selected])} EXISTS")
                self.send(f"* OK [UIDVALIDITY {mb.uidvalidity.get(selected, 1)}] ok")
                self.send(f"{tag} OK [{'READ-ONLY' if cmd == 'EXAMINE' else 'READ-WRITE'}] fatto")
            elif cmd == "CREATE":
                name = args.strip('"')
                if name in mb.folders:
                    self.send(f"{tag} NO esiste già")
                else:
                    mb.folders[name], mb.uidvalidity[name], mb.next_uid[name] = [], 1, 1
                    self.send(f"{tag} OK fatto")
            elif cmd == "UID":
                sub, _, uargs = args.partition(" ")
                self.uid(tag, sub.upper(), uargs, mb, selected)
            elif cmd == "APPEND":
                m = re.match(r'"?([^"]+)"? .*\{(\d+)\}$', args)
                self.send("+ pronto")
                data = self.rfile.read(int(m.group(2)))
                self.rfile.readline()
                mb.deliver(data, m.group(1), seen=True)
                self.send(f"{tag} OK fatto")
            elif cmd == "LOGOUT":
                self.send("* BYE")
                self.send(f"{tag} OK fatto")
                return
            else:
                self.send(f"{tag} OK fatto")

    def uid(self, tag: str, sub: str, args: str, mb: Mailbox, folder: str) -> None:
        msgs = mb.folders.get(folder, [])
        if sub == "SEARCH":
            m = re.search(r"UID (\d+):\*", args)
            if m:  # come i server veri: "n:*" restituisce almeno l'ultima mail
                uids = [x["uid"] for x in msgs if x["uid"] >= int(m.group(1))] or [x["uid"] for x in msgs[-1:]]
            else:
                uids = [x["uid"] for x in msgs]
            self.send("* SEARCH " + " ".join(map(str, uids)))
            self.send(f"{tag} OK fatto")
        elif sub == "FETCH":
            wanted = {int(u) for u in args.split(" ", 1)[0].split(",")}
            for seq, msg in enumerate(msgs, 1):
                if msg["uid"] in wanted:
                    raw = msg["raw"]
                    flags = " ".join(sorted(msg["flags"]))
                    self.send(f"* {seq} FETCH (UID {msg['uid']} FLAGS ({flags}) BODY[]<0> {{{len(raw)}}}".encode())
                    self.wfile.write(raw)
                    self.send(")")
            self.send(f"{tag} OK fatto")
        elif sub == "STORE":
            uid, _, rest = args.partition(" ")
            flag = re.search(r"\((.*)\)", rest).group(1)
            for msg in msgs:
                if msg["uid"] == int(uid):
                    msg["flags"].add(flag)
            self.send(f"{tag} OK fatto")
        elif sub in ("MOVE", "COPY"):
            uid, _, target = args.partition(" ")
            target = target.strip('"')
            for msg in list(msgs):
                if msg["uid"] == int(uid):
                    mb.deliver(msg["raw"], target, "\\Seen" in msg["flags"])
                    if sub == "MOVE":
                        msgs.remove(msg)
            self.send(f"{tag} OK fatto")
        elif sub == "EXPUNGE":
            mb.folders[folder] = [m for m in msgs if not (m["uid"] == int(args) and "\\Deleted" in m["flags"])]
            self.send(f"{tag} OK fatto")
        else:
            self.send(f"{tag} BAD")


class SMTPHandler(socketserver.StreamRequestHandler):
    def send(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")

    def handle(self) -> None:
        mb: Mailbox = self.server.mailbox
        sender, rcpts, authed = "", [], False
        self.send("220 fake SMTP")
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode().rstrip("\r\n")
            cmd = line.split(" ")[0].upper()
            if cmd in ("EHLO", "HELO"):
                self.wfile.write(b"250-localhost\r\n250-AUTH PLAIN XOAUTH2\r\n250 OK\r\n")
            elif cmd == "AUTH":
                mech, _, payload = line[5:].partition(" ")
                decoded = base64.b64decode(payload).decode()
                if mech.upper() == "PLAIN":
                    _, user, pw = decoded.split("\x00")
                else:
                    m = re.match(r"user=(.*)\x01auth=Bearer (.*)\x01\x01", decoded)
                    user, pw = (m.group(1), m.group(2)) if m else ("", "")
                authed = mb.users.get(user) == pw
                self.send("235 ok" if authed else "535 no")
            elif cmd == "MAIL":
                if not authed:
                    self.send("530 autenticazione richiesta")
                    continue
                sender = re.search(r"<(.*?)>", line).group(1)
                self.send("250 ok")
            elif cmd == "RCPT":
                rcpts.append(re.search(r"<(.*?)>", line).group(1))
                self.send("250 ok")
            elif cmd == "DATA":
                self.send("354 vai")
                data = b""
                while True:
                    chunk = self.rfile.readline()
                    if chunk in (b".\r\n", b""):
                        break
                    data += chunk
                mb.sent.append((sender, rcpts, data))
                rcpts = []
                self.send("250 inviata")
            elif cmd == "QUIT":
                self.send("221 ciao")
                return
            else:
                self.send("250 ok")


def start(mailbox: Mailbox, cert: Path, key: Path) -> tuple[socketserver.BaseServer, socketserver.BaseServer]:
    imap = _TLSServer(IMAPHandler, mailbox, cert, key)
    smtp = _TLSServer(SMTPHandler, mailbox, cert, key)
    for server in (imap, smtp):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return imap, smtp
