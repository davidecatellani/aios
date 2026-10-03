"""Password e token: nel portachiavi del sistema (Secret Service, via secret-tool).

Se il portachiavi non c'è (es. un sistema senza desktop), si usa un file leggibile
solo dall'utente (0600). Nell'immagine di AIOS il portachiavi è sempre presente.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .privacy import private_dir

SERVICE = "aios"


def _file() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios"
    return private_dir(base) / "secrets.json"


def _keyring() -> bool:
    return shutil.which("secret-tool") is not None and os.environ.get("AIOS_NO_KEYRING") != "1"


def store(key: str, value: str) -> None:
    if _keyring():
        proc = subprocess.run(["secret-tool", "store", "--label", f"AIOS {key}", "service", SERVICE, "key", key],
                              input=value, text=True, capture_output=True)
        if proc.returncode == 0:
            return
    path = _file()
    data = _read_file(path)
    data[key] = value
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)


def load(key: str) -> str | None:
    if _keyring():
        proc = subprocess.run(["secret-tool", "lookup", "service", SERVICE, "key", key], text=True, capture_output=True)
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    return _read_file(_file()).get(key)


def delete(key: str) -> None:
    if _keyring():
        subprocess.run(["secret-tool", "clear", "service", SERVICE, "key", key], capture_output=True)
    path = _file()
    data = _read_file(path)
    if data.pop(key, None) is not None:
        path.write_text(json.dumps(data))


def _read_file(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
