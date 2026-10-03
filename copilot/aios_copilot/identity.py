"""Identità unica dell'utente per tutti i suoi dispositivi.

- **Chiave principale** dell'utente (Ed25519), nata da un segreto di 128 bit che
  l'utente conserva come **frase di recupero** di 17 parole italiane (16 + controllo).
  Con la frase si ricrea la stessa identità su un dispositivo nuovo.
- **Chiave del dispositivo**: ognuno ha la sua; la chiave principale gli firma un
  **certificato** (nome, tipo, chiave pubblica). Un dispositivo con certificato valido
  è riconosciuto da tutti gli altri dell'utente; uno **revocato** non più.
- Dalla stessa frase si ricava la **chiave di sincronizzazione** (sync.py): i dati
  sincronizzati sono cifrati end-to-end, illeggibili per chiunque altro.
- Le richieste tra dispositivi sono firmate con la chiave del dispositivo (con
  l'orario, contro le ripetizioni).

Segreti nella cassaforte del sistema (vault.py); su disco solo la parte pubblica.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from . import ed25519, vault
from .crypto import hkdf

MASTER_SECRET = "identita-segreto"
DEVICE_SEED = "identita-dispositivo"
SYNC_KEY = "identita-sincronizzazione"
REQUEST_WINDOW = 120  # secondi di tolleranza tra gli orologi

# 256 parole comuni di almeno 4 lettere, diverse nelle prime 4: basta scrivere l'inizio.
WORDS = """
abete acqua aereo agosto albero alce alga allegro alpino amaca amico anatra ancora anello angolo
anguria anima antenna aprile aquila arancia arco argento aria armadio arpa asino atlante autunno
avena azzurro bacio balena bambola banana barca basilico becco biscotto bosco botte braccio brezza
brodo bruco bussola caffè calcio camino campana candela cane canoa cappello capra carota casa
castello cavallo cedro cena cervo cesto chiave chitarra cielo ciliegia cipolla civetta cocco coda
coniglio coppa corda corvo cuore cupola cuscino dado daino danza delfino dente deserto diamante dito
divano dolce domenica drago duna edera elefante erba estate fagiolo falco farfalla faro fata
febbraio felce ferro festa fiamma fico filo fiore fiume foglia fontana forno fragola freccia frutta
fulmine fumo fungo gabbiano gallo gamba gatto gelato ghianda giacca giardino giglio ginepro giraffa
giugno globo goccia gomma gondola grano grillo guanto gufo isola lago lampada lana lavanda leone
lepre letto libro limone lince lucciola luna lupo macchina madre maglia mandorla mare margherita
marmo mattone mela menta miele mirtillo montagna mulino musica naso nave nebbia neve nido noce nonno
notte nuvola oasi occhio oliva ombrello onda orso ottobre ovest pace padella palla pane panda
papavero parco pasta patata pavone penna pera pesce pigna pino pioggia piuma pizza ponte porta prato
quadro quercia radice ragno rana razzo remo riccio riso roccia rosa ruota sabbia sale salvia sasso
scala scoglio sedia sentiero serpente sole sorriso specchio spiga stella strada tavolo tazza teatro
tenda terra tigre topo torre treno trota tulipano uccello uovo valle vaso vela vento verde vetro
viola volpe zaino zebra zucca
""".split()
assert len(WORDS) == 256 and len({w[:4] for w in WORDS}) == 256 and min(map(len, WORDS)) >= 4


class IdentityError(ValueError):
    pass


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def unb64(text: str) -> bytes:
    return base64.b64decode(text.encode(), validate=True)


# --- frase di recupero --------------------------------------------------------------------------


def phrase_from_secret(secret: bytes) -> list[str]:
    assert len(secret) == 16
    check = hashlib.sha256(secret).digest()[0]
    return [WORDS[b] for b in secret + bytes([check])]


def secret_from_phrase(phrase: str) -> bytes:
    by_prefix = {w[:4]: i for i, w in enumerate(WORDS)}
    words = phrase.lower().replace(",", " ").split()
    if len(words) != 17:
        raise IdentityError(f"la frase di recupero ha 17 parole (ne ho lette {len(words)})")
    values = []
    for w in words:
        if w[:4] not in by_prefix:
            raise IdentityError(f"«{w}» non è una parola della frase di recupero")
        values.append(by_prefix[w[:4]])
    secret = bytes(values[:16])
    if hashlib.sha256(secret).digest()[0] != values[16]:
        raise IdentityError("la frase di recupero non torna: controlla l'ordine e le parole")
    return secret


def master_seed(secret: bytes) -> bytes:
    return hkdf(secret, b"aios-identita-v1")


def sync_key_from(secret: bytes) -> bytes:
    return hkdf(secret, b"aios-sincronizzazione-v1")


# --- certificati -------------------------------------------------------------------------------


def canonical(doc: dict) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def device_id(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()[:16]


@dataclass
class Certificate:
    id: str
    name: str
    kind: str  # pc | telefono | tablet
    public: str  # base64
    issued: int
    signature: str = ""

    def body(self) -> dict:
        return {"v": 1, "id": self.id, "name": self.name, "kind": self.kind, "public": self.public, "issued": self.issued}

    def to_dict(self) -> dict:
        return {**self.body(), "signature": self.signature}

    @classmethod
    def from_dict(cls, d: dict) -> Certificate:
        try:
            return cls(str(d["id"]), str(d["name"])[:60], str(d["kind"]), str(d["public"]), int(d["issued"]), str(d["signature"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise IdentityError("certificato non valido") from exc


def issue(master: bytes, public: bytes, name: str, kind: str, now: float | None = None) -> Certificate:
    cert = Certificate(device_id(public), name[:60] or "dispositivo", kind if kind in ("pc", "telefono", "tablet") else "pc",
                       b64(public), int(now or time.time()))
    cert.signature = b64(ed25519.sign(master, canonical(cert.body())))
    return cert


def revocation_list(master: bytes, revoked: list[str], seq: int) -> dict:
    body = {"v": 1, "revoked": sorted(set(revoked)), "seq": seq}
    return {**body, "signature": b64(ed25519.sign(master, canonical(body)))}


def valid_revocations(doc: dict, user_public: bytes) -> bool:
    body = {k: doc.get(k) for k in ("v", "revoked", "seq")}
    try:
        return ed25519.verify(user_public, canonical(body), unb64(doc.get("signature", "")))
    except ValueError:
        return False


def verify_certificate(cert: Certificate, user_public: bytes, revoked: set[str] = frozenset()) -> bool:
    try:
        public = unb64(cert.public)
        ok = ed25519.verify(user_public, canonical(cert.body()), unb64(cert.signature))
    except ValueError:
        return False
    return ok and cert.id == device_id(public) and cert.id not in revoked


# --- l'identità su questo dispositivo ------------------------------------------------------------


def config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "identita.json"


class Identity:
    """Ciò che questo dispositivo sa dell'identità: parte pubblica su disco, segreti nella cassaforte."""

    def __init__(self, data: dict):
        self.data = data

    # stato --------------------------------------------------------------------------------------
    @classmethod
    def load(cls) -> Identity | None:
        try:
            return cls(json.loads(config_path().read_text()))
        except (OSError, ValueError):
            return None

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))
        os.chmod(path, 0o600)

    @property
    def user_public(self) -> bytes:
        return unb64(self.data["utente"])

    @property
    def name(self) -> str:
        return self.data.get("nome", "")

    @property
    def certificate(self) -> Certificate:
        return Certificate.from_dict(self.data["certificato"])

    @property
    def revoked(self) -> set[str]:
        return set(self.data.get("revoche", {}).get("revoked", []))

    @property
    def devices(self) -> list[Certificate]:
        return [Certificate.from_dict(c) for c in self.data.get("dispositivi", [])]

    def master(self) -> bytes | None:
        """Il segreto principale, solo sui dispositivi che lo custodiscono (il primo, o dopo il recupero)."""
        secret = vault.load(MASTER_SECRET)
        return master_seed(unb64(secret.strip())) if secret else None

    def device_seed(self) -> bytes:
        seed = vault.load(DEVICE_SEED)
        if not seed:
            raise IdentityError("chiave del dispositivo mancante")
        return unb64(seed.strip())

    def sync_key(self) -> bytes | None:
        key = vault.load(SYNC_KEY)
        return unb64(key.strip()) if key else None

    # dispositivi ---------------------------------------------------------------------------------
    def add_device(self, public: bytes, name: str, kind: str) -> Certificate:
        if len(public) != 32:
            raise IdentityError("chiave del dispositivo non valida")
        master = self.master()
        if master is None:
            raise IdentityError("questo dispositivo non custodisce la chiave principale: abbina dal dispositivo principale")
        cert = issue(master, public, name, kind)
        self.data["dispositivi"] = [c for c in self.data.get("dispositivi", []) if c["id"] != cert.id] + [cert.to_dict()]
        self.save()
        return cert

    def revoke(self, name_or_id: str) -> list[Certificate]:
        master = self.master()
        if master is None:
            raise IdentityError("le revoche si fanno dal dispositivo che custodisce la chiave principale")
        wanted = name_or_id.lower().strip()
        hits = [c for c in self.devices if c.id != self.certificate.id and (wanted == c.id or wanted in c.name.lower())]
        if hits:
            seq = int(self.data.get("revoche", {}).get("seq", 0)) + 1
            self.data["revoche"] = revocation_list(master, list(self.revoked | {c.id for c in hits}), seq)
            self.save()
        return hits

    def accept_revocations(self, doc: dict) -> bool:
        """Revoche arrivate da un altro dispositivo: solo se firmate dall'utente e più recenti."""
        if not valid_revocations(doc, self.user_public) or doc["seq"] <= int(self.data.get("revoche", {}).get("seq", 0)):
            return False
        self.data["revoche"] = doc
        self.save()
        return True

    def trusts(self, cert: Certificate) -> bool:
        return verify_certificate(cert, self.user_public, self.revoked)

    def bundle_for(self, cert: Certificate) -> dict:
        """Cosa riceve un dispositivo appena abbinato (sul canale cifrato e verificato)."""
        key = self.sync_key()
        return {"utente": self.data["utente"], "nome": self.name, "certificato": cert.to_dict(),
                "revoche": self.data.get("revoche", {}), "dispositivi": self.data.get("dispositivi", []),
                "sincronizzazione": b64(key) if key else ""}

    # richieste firmate ----------------------------------------------------------------------------
    def sign_request(self, method: str, path: str, body: bytes, now: float | None = None) -> str:
        ts = str(int(now or time.time()))
        message = f"{method}\n{path}\n{ts}\n{hashlib.sha256(body).hexdigest()}".encode()
        signature = b64(ed25519.sign(self.device_seed(), message))
        cert = base64.urlsafe_b64encode(canonical(self.certificate.to_dict())).decode()
        return f"AIOS {cert}.{ts}.{signature}"

    def check_request(self, header: str, method: str, path: str, body: bytes, now: float | None = None) -> Certificate | None:
        """Il dispositivo che firma la richiesta, se è dell'utente, non revocato e l'orario torna."""
        try:
            scheme, _, rest = header.partition(" ")
            cert_b64, ts, signature = rest.split(".")
            if scheme != "AIOS" or abs((now or time.time()) - int(ts)) > REQUEST_WINDOW:
                return None
            cert = Certificate.from_dict(json.loads(base64.urlsafe_b64decode(cert_b64)))
            message = f"{method}\n{path}\n{ts}\n{hashlib.sha256(body).hexdigest()}".encode()
            if self.trusts(cert) and ed25519.verify(unb64(cert.public), message, unb64(signature)):
                return cert
        except (ValueError, IdentityError):
            return None
        return None


def _new_device_key() -> bytes:
    seed = secrets.token_bytes(32)
    vault.store(DEVICE_SEED, b64(seed))
    return ed25519.public_key(seed)


def create(name: str, device_name: str | None = None, kind: str = "pc") -> tuple[Identity, list[str]]:
    """Nuova identità: restituisce anche la frase di recupero da scrivere su carta."""
    return _from_secret(secrets.token_bytes(16), name, device_name, kind)


def restore(phrase: str, device_name: str | None = None, kind: str = "pc", name: str = "") -> Identity:
    """Su un dispositivo nuovo, dalla frase di recupero: stessa identità, stessi dati sincronizzati."""
    identity, _ = _from_secret(secret_from_phrase(phrase), name, device_name, kind)
    return identity


def _from_secret(secret: bytes, name: str, device_name: str | None, kind: str) -> tuple[Identity, list[str]]:
    master = master_seed(secret)
    vault.store(MASTER_SECRET, b64(secret))
    vault.store(SYNC_KEY, b64(sync_key_from(secret)))
    public = _new_device_key()
    cert = issue(master, public, device_name or socket.gethostname(), kind)
    identity = Identity({"utente": b64(ed25519.public_key(master)), "nome": name, "certificato": cert.to_dict(),
                         "dispositivi": [cert.to_dict()], "revoche": revocation_list(master, [], 0)})
    identity.save()
    return identity, phrase_from_secret(secret)


def join(bundle: dict) -> Identity:
    """Un dispositivo appena abbinato entra nell'identità (riceve certificato e chiave di sincronizzazione)."""
    identity = Identity({"utente": bundle["utente"], "nome": bundle.get("nome", ""), "certificato": bundle["certificato"],
                         "dispositivi": bundle.get("dispositivi", []), "revoche": bundle.get("revoche", {})})
    if not identity.trusts(identity.certificate):
        raise IdentityError("certificato non firmato dall'utente")
    if bundle.get("sincronizzazione"):
        vault.store(SYNC_KEY, bundle["sincronizzazione"])
    identity.save()
    return identity


def device_public_key() -> bytes:
    """La chiave pubblica di questo dispositivo (creata se manca), da far firmare in un abbinamento."""
    seed = vault.load(DEVICE_SEED)
    return ed25519.public_key(unb64(seed.strip())) if seed else _new_device_key()


def recovery_phrase() -> list[str] | None:
    secret = vault.load(MASTER_SECRET)
    return phrase_from_secret(unb64(secret.strip())) if secret else None
