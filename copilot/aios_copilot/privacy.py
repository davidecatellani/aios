"""Cosa il copilota non deve mai leggere, e come riconoscere i segreti nei file.

Tre difese, dalla più grossolana alla più fine:
1. percorsi esclusi sempre (chiavi, password, profili del browser...);
2. cartelle escluse dall'utente ("non leggere la cartella Lavoro");
3. i blocchi di testo che contengono segreti riconoscibili non entrano nell'indice.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path

# Nomi di cartelle o file che non si indicizzano mai, in qualsiasi posizione.
ALWAYS_EXCLUDED = (
    # chiavi e credenziali
    ".ssh", ".gnupg", ".password-store", ".aws", ".azure", ".kube", ".docker", ".netrc",
    ".pgpass", ".git-credentials", "keyrings", ".pki", ".vault-token",
    # profili dei browser (cookie, sessioni, password salvate)
    ".mozilla", "google-chrome", "chromium", "BraveSoftware", "vivaldi", "epiphany",
    # posta e messaggi (in futuro: con consenso esplicito)
    ".thunderbird", "evolution", "Signal", "TelegramDesktop",
    # spazzatura e rigenerabili
    ".cache", ".Trash", "Trash", "node_modules", ".git", "__pycache__", ".venv", "venv",
    ".local/share/aios", ".var",
)
EXCLUDED_FILE_PATTERNS = (
    ".env", ".env.*", "*.kdbx", "*.kdb", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*",
    "id_ed25519*", "id_ecdsa*", "*.ovpn", "credentials*", "*secret*", "*password*", "*.wallet",
)

# Segreti riconoscibili nel testo: vengono sostituiti, il resto del documento resta cercabile.
REDACTED = "[segreto rimosso]"
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # chiavi AWS
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),  # token GitHub
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),  # chiavi API in stile sk-...
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"),  # token Slack
    re.compile(r"(?i)\b(?:password|passwd|pwd|api[_-]?key|secret|token)\s*[:=]\s*\S{6,}"),
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),  # possibili numeri di carta
    re.compile(r"\bIT\d{2}[A-Z]\d{10}[0-9A-Z]{12}\b"),  # IBAN italiano
]


def contains_secret(text: str) -> bool:
    return any(p.search(text) for p in SECRET_PATTERNS)


def redact_secrets(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def _config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "privacy.json"


def user_exclusions() -> list[Path]:
    try:
        data = json.loads(_config_path().read_text())
        return [Path(p).expanduser() for p in data.get("excluded", [])]
    except (OSError, ValueError, AttributeError):
        return []


def add_exclusion(path: Path) -> list[Path]:
    path = path.expanduser().resolve()
    current = user_exclusions()
    if path not in current:
        current.append(path)
    cfg = _config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"excluded": [str(p) for p in current]}, indent=2))
    return current


def is_excluded(path: Path, extra: list[Path] | None = None) -> bool:
    """True se il percorso (o una cartella che lo contiene) non va mai letto."""
    parts = path.parts
    as_text = path.as_posix()
    for name in ALWAYS_EXCLUDED:
        if "/" in name:
            if f"/{name}/" in f"{as_text}/":
                return True
        elif name in parts:
            return True
    if any(fnmatch.fnmatch(path.name.lower(), pat) for pat in EXCLUDED_FILE_PATTERNS):
        return True
    for excluded in user_exclusions() if extra is None else extra:
        if path == excluded or excluded in path.parents:
            return True
    return False


def private_dir(path: Path) -> Path:
    """Crea una cartella leggibile solo dall'utente (0700)."""
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path
