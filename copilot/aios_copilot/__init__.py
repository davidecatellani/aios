"""SoIA Copilot: l'assistente AI locale che gestisce il sistema in lingua naturale.

Il codice personale (codice.py): se l'utente ha fatto modificare SoIA a Nova, ha una sua copia del codice in
~/.local/share/aios/codice. Qui, prima di qualsiasi altro modulo, il pacchetto si sposta su quella copia: tutto
(schermata, Nova, app, servizi) gira dal codice personale. Se la copia è guasta (la shell non parte), è in conflitto
con un aggiornamento o si chiede AIOS_CODICE=base, resta il codice originale dell'immagine.
Questo file non si personalizza: è il punto fermo che sceglie quale codice usare.
"""

import json as _json
import os as _os
import sys as _sys
import time as _time
from pathlib import Path as _Path

__version__ = "0.1.0"

BASE_DIR = _Path(__file__).resolve().parent  # il codice dell'immagine (o del repository, in sviluppo)


def _personal_dir() -> "_Path | None":
    if _os.environ.get("AIOS_CODICE", "") == "base":
        return None
    try:
        home = _Path.home()
    except (KeyError, RuntimeError):
        return None
    state = _Path(_os.environ.get("XDG_STATE_HOME", home / ".local" / "state")) / "aios" / "codice.json"
    try:
        s = _json.loads(state.read_text())
    except (OSError, ValueError):
        return None
    if not s.get("attivo") or s.get("guasto") or s.get("conflitto"):
        return None
    # modalità sicura: la shell vera (avviata senza argomenti) conta gli avvii; quando funziona azzera il conto
    # (codice.healthy). Al terzo avvio non riuscito di fila si torna al codice originale.
    if _os.path.basename(_sys.argv[0] if _sys.argv else "") == "aios-shell" and len(_sys.argv) == 1:
        s["avvii"] = int(s.get("avvii", 0)) + 1
        if s["avvii"] >= 3:
            s.update(guasto=True, avvii=0, guasto_quando=int(_time.time()))
        try:
            tmp = state.with_suffix(".tmp")
            tmp.write_text(_json.dumps(s, indent=1))
            tmp.replace(state)
        except OSError:
            pass
        if s.get("guasto"):
            return None
    d =_Path(_os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "aios" / "codice" / "aios_copilot"
    try:
        if (d / "__init__.py").exists() and d.resolve() != BASE_DIR:
            return d
    except OSError:
        pass
    return None


PERSONAL_DIR = _personal_dir()
if PERSONAL_DIR is not None:
    __path__ = [str(PERSONAL_DIR)]  # i moduli si caricano dalla copia personale
