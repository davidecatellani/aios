#!/usr/bin/env bash
# Funzioni comuni agli script di AIOS per telefono.
set -euo pipefail
QUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PHONE="$(dirname "$QUI")"
SORGENTI="${AIOS_SORGENTI:-$HOME/aios-android}"   # ~300 GB liberi
CHIAVI="${AIOS_CHIAVI:-$HOME/.aios-chiavi}"       # MAI dentro il repository

campo() {  # campo BERSAGLIO CHIAVE → valore da dispositivi.json
  python3 -c 'import json,sys; t=json.load(open(sys.argv[1]))["bersagli"][sys.argv[2]]; print(t.get(sys.argv[3], ""))' \
    "$PHONE/dispositivi.json" "$1" "$2"
}

bersaglio_valido() {
  python3 -c 'import json,sys; sys.exit(0 if sys.argv[2] in json.load(open(sys.argv[1]))["bersagli"] else 1)' \
    "$PHONE/dispositivi.json" "$1" || { echo "Bersaglio sconosciuto: $1 (vedi dispositivi.json)" >&2; exit 1; }
}
