"""Sincronizzazione cifrata end-to-end tra i dispositivi dell'utente.

Modello: un CRDT «vince l'ultima modifica» per chiave (LWW-map). Ogni modifica è
un'operazione con un orologio logico ibrido (HLC: millisecondi, contatore,
dispositivo), quindi tutti i dispositivi scelgono la stessa versione senza conflitti,
anche dopo giorni offline; le cancellazioni sono operazioni come le altre (valore
None), così non «risorgono».

Riservatezza: chiave e valore viaggiano cifrati (ChaCha20-Poly1305) con la chiave di
sincronizzazione dell'identità; in chiaro restano solo un'impronta opaca della chiave e
l'orologio. Un dispositivo intermedio o un futuro relay non può leggere nulla.

Cosa si sincronizza (adattatori): agenda e promemoria, nome dell'utente, temi creati
e tema in uso. Non si sincronizza ciò che dipende dal dispositivo (modelli AI, cartelle
escluse, posta: ogni dispositivo la scarica da sé).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from .crypto import DecryptError, open_sealed, seal
from .privacy import private_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS ops (hkey TEXT PRIMARY KEY, ms INTEGER, ctr INTEGER, dev TEXT, box TEXT, seq INTEGER);
CREATE INDEX IF NOT EXISTS ops_seq ON ops(seq);
CREATE TABLE IF NOT EXISTS snapshot (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS peers (peer TEXT PRIMARY KEY, received INTEGER DEFAULT 0, sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""
MAX_OPS = 2000  # per richiesta


class Adapter(Protocol):
    prefix: str

    def records(self) -> dict[str, Any]: ...

    def apply(self, key: str, value: Any) -> None: ...


class Clock:
    """Orologio logico ibrido: sempre crescente, anche se l'ora del dispositivo torna indietro."""

    def __init__(self, device: str, wall: Callable[[], float] = time.time):
        self.device, self.wall = device, wall
        self.ms, self.ctr = 0, 0

    def now(self) -> tuple[int, int, str]:
        ms = int(self.wall() * 1000)
        if ms > self.ms:
            self.ms, self.ctr = ms, 0
        else:
            self.ctr += 1
        return self.ms, self.ctr, self.device

    def observe(self, ms: int, ctr: int) -> None:
        if (ms, ctr) > (self.ms, self.ctr):
            self.ms, self.ctr = ms, ctr


class SyncEngine:
    def __init__(self, key: bytes, device: str, adapters: list[Adapter], db_path: Path | None = None,
                 wall: Callable[[], float] = time.time):
        if db_path is None:
            db_path = private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios") / "sync.db"
        new = not db_path.exists()
        self.db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        if new:
            os.chmod(db_path, 0o600)
        self.db.executescript(SCHEMA)
        self.key, self.device = key, device
        self.adapters = {a.prefix: a for a in adapters}
        self.clock = Clock(device, wall)
        row = self.db.execute("SELECT MAX(ms) FROM ops").fetchone()
        self.clock.ms = row[0] or 0
        self.lock = threading.RLock()

    def rekey(self, key: bytes) -> None:
        """Nuova chiave di sincronizzazione: si ricifra tutto con gli stessi orologi (nessuna modifica persa)
        e si ricomincia lo scambio con tutti da capo."""
        with self.lock:
            if key == self.key:
                return
            old_key, rows = self.key, self.db.execute("SELECT hkey, ms, ctr, dev, box FROM ops").fetchall()
            self.key = key
            self.db.execute("DELETE FROM ops")
            for hkey, ms, ctr, dev, box in rows:
                try:
                    doc = json.loads(open_sealed(old_key, base64.b64decode(box), self._aad(hkey, ms, ctr, dev)))
                except (ValueError, DecryptError):
                    continue
                new_h = self._hkey(doc["k"])
                sealed = seal(key, json.dumps(doc).encode(), self._aad(new_h, ms, ctr, dev))
                self._store(new_h, ms, ctr, dev, base64.b64encode(sealed).decode())
            self.db.execute("DELETE FROM peers")

    # --- utilità ------------------------------------------------------------------------------
    def _hkey(self, key: str) -> str:
        return hmac.new(self.key, key.encode(), hashlib.sha256).hexdigest()[:32]

    def _next_seq(self) -> int:
        row = self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM ops").fetchone()
        return row[0] + 1

    @staticmethod
    def _aad(hkey: str, ms: int, ctr: int, dev: str) -> bytes:
        return f"{hkey}|{ms}|{ctr}|{dev}".encode()

    def _store(self, hkey: str, ms: int, ctr: int, dev: str, box: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO ops VALUES (?, ?, ?, ?, ?, ?)", (hkey, ms, ctr, dev, box, self._next_seq()))

    def _snapshot(self, key: str, value: Any) -> None:
        if value is None:
            self.db.execute("DELETE FROM snapshot WHERE key = ?", (key,))
        else:
            self.db.execute("INSERT OR REPLACE INTO snapshot VALUES (?, ?)", (key, json.dumps(value, sort_keys=True)))

    # --- modifiche locali -----------------------------------------------------------------------
    def scan(self) -> int:
        """Confronta i dati di ogni adattatore con l'ultima fotografia: le differenze diventano operazioni."""
        with self.lock:
            known = dict(self.db.execute("SELECT key, value FROM snapshot"))
            current: dict[str, Any] = {}
            for prefix, adapter in self.adapters.items():
                try:
                    current.update({f"{prefix}/{k}": v for k, v in adapter.records().items()})
                except Exception:
                    known = {k: v for k, v in known.items() if not k.startswith(prefix + "/")}  # adattatore guasto: nessuna cancellazione
            changes = [(k, v) for k, v in current.items() if known.get(k) != json.dumps(v, sort_keys=True)]
            changes += [(k, None) for k in known if k not in current]
            for key, value in changes:
                self._local(key, value)
            return len(changes)

    def _local(self, key: str, value: Any) -> None:
        ms, ctr, dev = self.clock.now()
        hkey = self._hkey(key)
        box = seal(self.key, json.dumps({"k": key, "v": value}).encode(), self._aad(hkey, ms, ctr, dev))
        self._store(hkey, ms, ctr, dev, base64.b64encode(box).decode())
        self._snapshot(key, value)

    # --- scambio --------------------------------------------------------------------------------
    def ops_since(self, seq: int) -> tuple[list[dict], int]:
        rows = self.db.execute("SELECT hkey, ms, ctr, dev, box, seq FROM ops WHERE seq > ? ORDER BY seq LIMIT ?",
                               (seq, MAX_OPS)).fetchall()
        ops = [{"h": h, "ms": ms, "c": c, "d": d, "box": box} for h, ms, c, d, box, _ in rows]
        return ops, (rows[-1][5] if rows else seq)

    def merge(self, ops: list[dict]) -> int:
        """Operazioni da un altro dispositivo: vince la più recente; si applica ai dati locali."""
        applied = 0
        with self.lock:
            self.scan()  # prima le modifiche locali ancora non registrate, così si confrontano ad armi pari
            for op in ops[:MAX_OPS]:
                try:
                    hkey, ms, ctr, dev = str(op["h"]), int(op["ms"]), int(op["c"]), str(op["d"])
                    box = base64.b64decode(op["box"])
                    doc = json.loads(open_sealed(self.key, box, self._aad(hkey, ms, ctr, dev)))
                    key, value = str(doc["k"]), doc["v"]
                except (KeyError, ValueError, TypeError, DecryptError):
                    continue  # alterata o di un'altra identità: ignorata
                if self._hkey(key) != hkey:
                    continue
                current = self.db.execute("SELECT ms, ctr, dev FROM ops WHERE hkey = ?", (hkey,)).fetchone()
                if current and tuple(current) >= (ms, ctr, dev):
                    continue
                self.clock.observe(ms, ctr)
                self._store(hkey, ms, ctr, dev, op["box"])
                prefix, _, sub = key.partition("/")
                adapter = self.adapters.get(prefix)
                if adapter is not None:
                    try:
                        adapter.apply(sub, value)
                    except Exception:
                        continue
                self._snapshot(key, value)
                applied += 1
            return applied

    def peer(self, peer: str) -> tuple[int, int]:
        row = self.db.execute("SELECT received, sent FROM peers WHERE peer = ?", (peer,)).fetchone()
        return (row[0], row[1]) if row else (0, 0)

    def set_peer(self, peer: str, received: int | None = None, sent: int | None = None) -> None:
        r, s = self.peer(peer)
        self.db.execute("INSERT OR REPLACE INTO peers VALUES (?, ?, ?)", (peer, r if received is None else received,
                                                                         s if sent is None else sent))

    def sync_with(self, peer: str, request: Callable[[str, str, Any], Any]) -> tuple[int, int]:
        """Scambio completo con un altro dispositivo. → (ricevute e applicate, inviate)."""
        with self.lock:
            self.scan()
            received, sent = self.peer(peer)
            reply = request("GET", f"/api/sync?dopo={received}", None)
            applied = self.merge(reply.get("ops", []))
            self.set_peer(peer, received=int(reply.get("seq", received)))
            ops, last = self.ops_since(sent)
            ops = [op for op in ops if op["d"] != peer]  # quelle che vengono da lui le ha già
            if ops:
                request("POST", "/api/sync", {"ops": ops})
            self.set_peer(peer, sent=last)
            return applied, len(ops)


# --- adattatori -------------------------------------------------------------------------------------


class AgendaAdapter:
    prefix = "agenda"

    def __init__(self, agenda: Any):
        self.agenda = agenda

    def records(self) -> dict[str, Any]:
        return self.agenda.sync_records()

    def apply(self, key: str, value: Any) -> None:
        self.agenda.apply_record(key, value)


class ProfileAdapter:
    prefix = "profilo"
    FIELDS = ("name",)  # solo ciò che vale per la persona, non per il dispositivo

    def records(self) -> dict[str, Any]:
        from .welcome import load_profile

        profile = load_profile()
        return {f: profile[f] for f in self.FIELDS if profile.get(f)}

    def apply(self, key: str, value: Any) -> None:
        from .welcome import save_profile

        if key in self.FIELDS:
            save_profile(**{key: value or ""})


class ThemesAdapter:
    """Temi creati (con sfondo «ricetta») e tema in uso, applicato anche sugli altri dispositivi."""

    prefix = "temi"

    def __init__(self, apply_theme: Callable[[Any], Any] | None = None):
        self.apply_theme = apply_theme

    def records(self) -> dict[str, Any]:
        from . import themeapply, themes

        out: dict[str, Any] = {}
        for t in themes.installed():
            if t.wallpaper.get("kind") != "immagine":  # le immagini restano sul dispositivo (per ora)
                doc = json.loads(t.to_json())
                doc.pop("origin", None)  # dipende dal dispositivo: non va confrontato (eviterebbe un rimbalzo continuo)
                out[t.id] = doc
        out["_attuale"] = themeapply.current_id()
        return out

    def apply(self, key: str, value: Any) -> None:
        from . import themeapply, themes
        from .thememarket import validate

        if key == "_attuale":
            theme = themeapply.find(str(value)) if value else None
            if theme is not None and theme.id != themeapply.current_id():
                (self.apply_theme or themeapply.apply)(theme)
            return
        if value is None:
            folder = themes.themes_dir() / key
            if (folder / "theme.json").exists():
                (folder / "theme.json").unlink()
            return
        theme = validate(dict(value))  # anche dai propri dispositivi: solo campi e colori validi
        themes.save(theme)


class SettingsAdapter:
    """Impostazioni che valgono per tutti i dispositivi dell'utente (per ora: il relay)."""

    prefix = "impostazioni"

    def records(self) -> dict[str, Any]:
        from .identity import Identity

        me = Identity.load()
        return {"relay": me.data["relay"]} if me and me.data.get("relay") else {}

    def apply(self, key: str, value: Any) -> None:
        from .identity import Identity

        me = Identity.load()
        if me is None or key != "relay":
            return
        if value and isinstance(value, dict) and str(value.get("url", "")).startswith("https://"):
            me.data["relay"] = {"url": value["url"], "fingerprint": str(value.get("fingerprint", ""))}
        else:
            me.data.pop("relay", None)
        me.save()


def default_adapters() -> list[Adapter]:
    from .agenda import Agenda

    from .mesh.bluetooth import BluetoothAdapter

    return [AgendaAdapter(Agenda()), ProfileAdapter(), ThemesAdapter(), SettingsAdapter(), BluetoothAdapter()]


def engine_for(identity: Any, adapters: list[Adapter] | None = None) -> SyncEngine | None:
    key = identity.sync_key()
    if key is None:
        return None
    return SyncEngine(key, identity.certificate.id, adapters if adapters is not None else default_adapters())
