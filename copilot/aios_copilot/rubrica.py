"""La rubrica di SoIA: una sola, fatta da tre fonti senza che l'utente debba copiare niente.

- il telefono (la rubrica sincronizzata da KDE Connect, file .vcf);
- la posta (le persone con cui si scambiano mail: almeno due mail, niente «noreply»);
- i contatti aggiunti qui o da Nova («aggiungi Mario Rossi alla rubrica, 333 1234567»), in
  ~/.local/share/aios/rubrica.vcf: vCard, il formato che leggono tutti.

La stessa persona da più fonti diventa un solo contatto (stesso nome, numero o indirizzo).
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

NOREPLY = re.compile(r"no-?reply|do-?not-?reply|notifiche?@|notification|newsletter|mailer-daemon|info@|news@|marketing", re.I)


@dataclass
class Contact:
    nome: str
    telefoni: list[str] = field(default_factory=list)
    email: list[str] = field(default_factory=list)
    compleanno: str = ""  # AAAA-MM-GG o --MM-GG
    note: str = ""
    fonti: list[str] = field(default_factory=list)  # telefono | posta | aios
    mail_scambiate: int = 0

    @property
    def key(self) -> str:
        return re.sub(r"\s+", " ", self.nome.strip().lower())


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))


def own_path() -> Path:
    return data_dir() / "aios" / "rubrica.vcf"


def _unfold(text: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", text)


def _unescape(v: str) -> str:
    return v.replace("\\,", ",").replace("\\;", ";").replace("\\n", "\n").replace("\\\\", "\\").strip()


def parse_vcf(text: str, source: str) -> list[Contact]:
    out = []
    for card in _unfold(text).split("BEGIN:VCARD")[1:]:
        fields: dict[str, list[str]] = {}
        for line in card.splitlines():
            key, sep, value = line.partition(":")
            if not sep:
                continue
            name = key.split(";", 1)[0].split(".")[-1].upper()
            fields.setdefault(name, []).append(_unescape(value))
        name = (fields.get("FN") or [""])[0]
        if not name and fields.get("N"):
            parts = [p for p in fields["N"][0].split(";") if p]
            name = " ".join(reversed(parts[:2]))
        tels = [t for t in fields.get("TEL", []) if re.search(r"\d{3}", t)]
        mails = [m.lower() for m in fields.get("EMAIL", []) if "@" in m]
        if not name:
            name = (mails or tels or [""])[0]
        if not name:
            continue
        bday = (fields.get("BDAY") or [""])[0]
        if re.fullmatch(r"\d{8}", bday):
            bday = f"{bday[:4]}-{bday[4:6]}-{bday[6:]}"
        out.append(Contact(name, list(dict.fromkeys(tels)), list(dict.fromkeys(mails)), bday,
                           (fields.get("NOTE") or [""])[0], [source]))
    return out


def to_vcf(contacts: Iterable[Contact]) -> str:
    def esc(v: str) -> str:
        return v.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    cards = []
    for c in contacts:
        lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{esc(c.nome)}"]
        lines += [f"TEL:{esc(t)}" for t in c.telefoni] + [f"EMAIL:{esc(e)}" for e in c.email]
        if c.compleanno:
            lines.append(f"BDAY:{c.compleanno}")
        if c.note:
            lines.append(f"NOTE:{esc(c.note)}")
        cards.append("\r\n".join(lines + ["END:VCARD"]))
    return "\r\n".join(cards) + ("\r\n" if cards else "")


def _digits(number: str) -> str:
    d = re.sub(r"[^\d+]", "", number)
    return ("+" + d[2:] if d.startswith("00") else d)[-9:]


def phone_contacts(base: Path | None = None) -> list[Contact]:
    out = []
    for vcf in sorted((base or data_dir() / "kpeoplevcard").glob("**/*.vcf")):
        try:
            out += parse_vcf(vcf.read_text(errors="replace"), "telefono")
        except OSError:
            continue
    return out


def mail_contacts(db_path: Path | None = None, minimum: int = 2) -> list[Contact]:
    path = db_path or data_dir() / "aios" / "mail.db"
    if not path.exists():
        return []
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = db.execute("SELECT sender_name, lower(sender), COUNT(*) FROM messages WHERE sender LIKE '%@%' "
                          "GROUP BY lower(sender) HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC LIMIT 400", (minimum,)).fetchall()
        db.close()
    except sqlite3.Error:
        return []
    out = []
    for name, address, n in rows:
        if NOREPLY.search(address) or NOREPLY.search(name or ""):
            continue
        name = (name or "").strip().strip('"') or address.split("@")[0].replace(".", " ").title()
        out.append(Contact(name, [], [address], fonti=["posta"], mail_scambiate=int(n)))
    return out


def own_contacts(path: Path | None = None) -> list[Contact]:
    try:
        return parse_vcf((path or own_path()).read_text(errors="replace"), "aios")
    except OSError:
        return []


def merge(*sources: Iterable[Contact]) -> list[Contact]:
    """Un solo contatto per persona: stesso nome, o stesso numero, o stesso indirizzo."""
    merged: list[Contact] = []
    by_key: dict[str, Contact] = {}
    for source in sources:
        for c in source:
            keys = [f"n:{c.key}"] + [f"t:{_digits(t)}" for t in c.telefoni if _digits(t)] + [f"e:{e}" for e in c.email]
            into = next((by_key[k] for k in keys if k in by_key), None)
            if into is None:
                into = Contact(c.nome)
                merged.append(into)
            for t in c.telefoni:
                if _digits(t) not in {_digits(x) for x in into.telefoni}:
                    into.telefoni.append(t)
            into.email += [e for e in c.email if e not in into.email]
            into.compleanno = into.compleanno or c.compleanno
            into.note = into.note or c.note
            into.fonti += [f for f in c.fonti if f not in into.fonti]
            into.mail_scambiate += c.mail_scambiate
            if "@" in into.nome and "@" not in c.nome:
                into.nome = c.nome
            for k in keys + [f"n:{into.key}"]:
                by_key.setdefault(k, into)
    return sorted(merged, key=lambda c: c.nome.lower())


class Rubrica:
    def __init__(self, phone: Callable[[], list[Contact]] = phone_contacts, mail: Callable[[], list[Contact]] = mail_contacts,
                 path: Path | None = None):
        self.phone, self.mail, self.path = phone, mail, path or own_path()

    def all(self) -> list[Contact]:
        return merge(own_contacts(self.path), self.phone(), self.mail())

    def as_json(self) -> list[dict[str, Any]]:
        return [asdict(c) for c in self.all()]

    def find(self, query: str) -> list[Contact]:
        q = query.lower().strip()
        digits = _digits(q) if re.search(r"\d{3}", q) else ""
        return [c for c in self.all() if q in c.nome.lower() or any(q in e for e in c.email)
                or (digits and any(_digits(t) == digits for t in c.telefoni))]

    def add(self, nome: str, telefono: str = "", email: str = "", compleanno: str = "", note: str = "") -> str:
        nome = nome.strip()
        if not nome or len(nome) > 80:
            return "Mi serve il nome del contatto."
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
            return f"«{email}» non sembra un indirizzo email."
        own = own_contacts(self.path)
        c = next((x for x in own if x.key == nome.lower()), None)
        if c is None:
            c = Contact(nome, fonti=["aios"])
            own.append(c)
        if telefono.strip() and telefono.strip() not in c.telefoni:
            c.telefoni.append(telefono.strip())
        if email.strip() and email.strip().lower() not in c.email:
            c.email.append(email.strip().lower())
        c.compleanno = compleanno.strip() or c.compleanno
        c.note = note.strip() or c.note
        self._save(own)
        return f"{nome} è in rubrica."

    def remove(self, nome: str) -> bool:
        own = own_contacts(self.path)
        keep = [c for c in own if c.key != nome.strip().lower()]
        if len(keep) == len(own):
            return False
        self._save(keep)
        return True

    def _save(self, contacts: list[Contact]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(to_vcf(contacts))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)


def describe(c: Contact) -> str:
    bits = [c.nome]
    if c.telefoni:
        bits.append("tel. " + ", ".join(c.telefoni))
    if c.email:
        bits.append(", ".join(c.email))
    if c.compleanno:
        bits.append("compleanno " + c.compleanno.lstrip("-"))
    return " — ".join(bits)
