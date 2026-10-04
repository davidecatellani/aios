"""Riprendere da dove eri: programmi, file e siti aperti, al riavvio o su un altro dispositivo.

Ogni mezzo minuto la shell annota cosa è aperto (solo se è cambiato qualcosa):
- i programmi (Hyprland / wlrctl) e, se il titolo lo dice, il file aperto (cercato nei documenti
  recenti di GTK, ~/.local/share/recently-used.xbel);
- le schede di Firefox (dal suo salvataggio di sessione; le finestre anonime non ci sono mai).

Allo spegnimento le finestre si chiudono una dopo l'altra: per questo si tengono gli ultimi
salvataggi e, al riavvio, si sceglie l'ultimo fatto almeno SETTLE secondi prima che la sessione finisse.
La sessione viaggia cifrata tra i dispositivi dell'utente (sync.py, SessionAdapter): sull'altro PC
Nova riapre i siti, e programmi e file se ci sono anche lì.
"""

from __future__ import annotations

import json
import os
import re
import socket
import struct
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

KEEP = 12
SETTLE = 45.0  # i salvataggi fatti negli ultimi secondi prima dello spegnimento sono «a metà chiusura»
FILE_IN_TITLE = re.compile(r"([^/\\:*?\"<>|—–\-]+?\.[A-Za-z0-9]{2,5})\b")


def data_dir() -> Path:
    from .agenda import data_dir as dd
    from .privacy import private_dir

    return private_dir(dd())


def device_name() -> str:
    return socket.gethostname() or "questo computer"


# --- Firefox: schede aperte ----------------------------------------------------------------------------
def lz4_block(src: bytes, size: int) -> bytes:
    """Decompressore LZ4 (formato a blocchi) minimo, per i file .jsonlz4 di Firefox."""
    out = bytearray()
    i = 0
    while i < len(src):
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= len(src):
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        match = token & 15
        if match == 15:
            while True:
                b = src[i]
                i += 1
                match += b
                if b != 255:
                    break
        match += 4
        start = len(out) - offset
        for k in range(match):  # può sovrapporsi: si copia un byte alla volta
            out.append(out[start + k])
    return bytes(out[:size])


def read_mozlz4(path: Path) -> Any:
    data = path.read_bytes()
    if not data.startswith(b"mozLz40\0"):
        raise ValueError("non è un file mozlz4")
    size = struct.unpack("<I", data[8:12])[0]
    return json.loads(lz4_block(data[12:], size))


def firefox_tabs(home: Path | None = None, limit: int = 30) -> list[dict[str, str]]:
    home = home or Path.home()
    tabs: list[dict[str, str]] = []
    for base in (home / ".mozilla/firefox", home / ".var/app/org.mozilla.firefox/.mozilla/firefox"):
        for f in sorted(base.glob("*/sessionstore-backups/recovery.jsonlz4")):
            try:
                data = read_mozlz4(f)
            except (OSError, ValueError, struct.error, IndexError):
                continue
            for win in data.get("windows", []):
                for tab in win.get("tabs", []):
                    entries = tab.get("entries") or []
                    if not entries:
                        continue
                    cur = entries[min(max(int(tab.get("index", len(entries))) - 1, 0), len(entries) - 1)]
                    url = cur.get("url", "")
                    if url.startswith(("http://", "https://")):
                        tabs.append({"titolo": cur.get("title", "")[:120], "url": url})
    return tabs[:limit]


# --- file aperti nei programmi -------------------------------------------------------------------------
def recent_documents(home: Path | None = None, limit: int = 300) -> list[Path]:
    home = home or Path.home()
    xbel = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "recently-used.xbel"
    try:
        root = ET.parse(xbel).getroot()
    except (OSError, ET.ParseError):
        return []
    items = []
    for b in root.findall("bookmark"):
        href = b.get("href", "")
        if href.startswith("file://"):
            items.append((b.get("visited") or b.get("modified") or "", Path(urllib.parse.unquote(href[7:]))))
    items.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in items[:limit]]


def file_for_title(title: str, recent: list[Path]) -> str:
    """Il file aperto in una finestra, se il titolo contiene un nome di file tra i documenti recenti."""
    for m in FILE_IN_TITLE.finditer(title):
        name = m.group(1).strip()
        for p in recent:
            if p.name == name and p.exists():
                return str(p)
    return ""


# --- istantanea ----------------------------------------------------------------------------------------
def snapshot(windows: list[dict[str, str]], apps: dict[str, Any], recent: list[Path] | None = None,
             tabs: list[dict[str, str]] | None = None, now: float | None = None) -> dict[str, Any]:
    recent = recent_documents() if recent is None else recent
    programs = []
    for w in windows:
        app_id = w.get("app_id", "")
        desktop = app_id if app_id in apps else next((k for k in apps if k.lower() == app_id.lower()
                                                     or k.lower().endswith("." + app_id.lower())), "")
        name = getattr(apps.get(desktop), "name", "") or app_id
        programs.append({"app": desktop or app_id, "nome": name, "titolo": w.get("title", "")[:120],
                         "file": file_for_title(w.get("title", ""), recent)})
    return {"quando": now or time.time(), "dispositivo": device_name(), "programmi": programs,
            "siti": firefox_tabs() if tabs is None else tabs}


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a.get("programmi") == b.get("programmi") and a.get("siti") == b.get("siti")


class Sessions:
    """Gli ultimi salvataggi di questo dispositivo, più quelli arrivati dagli altri (sync)."""

    def __init__(self, folder: Path | None = None, clock: Callable[[], float] = time.time):
        self.folder = folder or data_dir()
        self.clock = clock

    @property
    def path(self) -> Path:
        return self.folder / "sessioni.json"

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict):
                data.setdefault("storia", [])
                data.setdefault("altri", {})
                return data
        except (OSError, ValueError):
            pass
        return {"storia": [], "altri": {}}

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".nuovo")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def record(self, snap: dict[str, Any]) -> bool:
        """Salva l'istantanea se è cambiato qualcosa; aggiorna sempre il «battito» (sessione viva)."""
        data = self.load()
        data["battito"] = self.clock()
        changed = not data["storia"] or not _same(data["storia"][-1], snap)
        if changed:
            data["storia"] = (data["storia"] + [snap])[-KEEP:]
        self._save(data)
        return changed

    def last_session(self) -> dict[str, Any] | None:
        """La sessione da riprendere: l'ultimo salvataggio fatto prima che cominciasse lo spegnimento."""
        data = self.load()
        history = [s for s in data["storia"] if s.get("programmi") or s.get("siti")]
        if not history:
            return None
        end = data.get("battito", history[-1]["quando"])
        settled = [s for s in history if s["quando"] <= end - SETTLE]
        return (settled or history)[-1]

    def mark_offered(self, boot: float) -> None:
        data = self.load()
        data["proposta"] = boot
        self._save(data)

    def offered(self, boot: float) -> bool:
        return self.load().get("proposta", 0) >= boot

    # dagli altri dispositivi
    def remote(self) -> dict[str, dict[str, Any]]:
        return self.load()["altri"]

    def store_remote(self, device: str, snap: dict[str, Any] | None) -> None:
        if device == device_name():
            return
        data = self.load()
        if snap is None:
            data["altri"].pop(device, None)
        else:
            data["altri"][device] = snap
        self._save(data)


def describe(snap: dict[str, Any]) -> str:
    parts = []
    for p in snap.get("programmi", [])[:6]:
        parts.append(p["nome"] + (f" ({Path(p['file']).name})" if p.get("file") else ""))
    if snap.get("siti"):
        n = len(snap["siti"])
        parts.append(f"{n} {'scheda' if n == 1 else 'schede'} di Firefox")
    return ", ".join(parts)


def restore(snap: dict[str, Any], apps: dict[str, Any], launch: Callable[[list[str]], bool],
            open_windows: list[dict[str, str]] | None = None, same_device: bool = True) -> list[str]:
    """Riapre programmi (con il loro file) e siti. → cosa è stato riaperto.

    Sullo stesso PC Firefox riapre da sé le sue schede (preferenza di AIOS: riprendi la sessione);
    su un altro dispositivo le schede le apre AIOS."""
    done: list[str] = []
    already = {w.get("app_id", "").lower() for w in open_windows or []}
    for p in snap.get("programmi", []):
        app = p.get("app", "")
        if ("firefox" in app.lower() and not same_device) or app.lower() in already or app not in apps:
            continue
        target = p.get("file") if p.get("file") and Path(p["file"]).exists() else ""
        if launch(["gtk-launch", app] + ([target] if target else [])):
            done.append(p["nome"] + (f" con {Path(target).name}" if target else ""))
            already.add(app.lower())
    if snap.get("siti") and not same_device:
        urls = [s["url"] for s in snap["siti"][:20]]
        browser = next((a for a in apps if "firefox" in a.lower()), None)
        if launch(["gtk-launch", browser, *urls] if browser else ["xdg-open", urls[0]]):
            done.append(f"{len(urls)} {'sito' if len(urls) == 1 else 'siti'}")
    return done
