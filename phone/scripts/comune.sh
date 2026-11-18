#!/usr/bin/env bash
# Funzioni comuni agli script di SoIA per telefono.
set -euo pipefail
QUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PHONE="$(dirname "$QUI")"
SORGENTI="${AIOS_SORGENTI:-$HOME/aios-android}"   # ~300 GB liberi
CHIAVI="${AIOS_CHIAVI:-$HOME/.aios-chiavi}"       # MAI dentro il repository
BUILD_JOBS="${AIOS_BUILD_JOBS:-4}"               # prudente per il PC con 32 GB di RAM
[[ "$BUILD_JOBS" =~ ^[1-9][0-9]*$ ]] || { echo "AIOS_BUILD_JOBS deve essere un intero positivo." >&2; exit 1; }

campo() {  # campo BERSAGLIO CHIAVE → valore da dispositivi.json
  python3 -c 'import json,sys; t=json.load(open(sys.argv[1]))["bersagli"][sys.argv[2]]; print(t.get(sys.argv[3], ""))' \
    "$PHONE/dispositivi.json" "$1" "$2"
}

bersaglio_valido() {
  python3 -c 'import json,sys; sys.exit(0 if sys.argv[2] in json.load(open(sys.argv[1]))["bersagli"] else 1)' \
    "$PHONE/dispositivi.json" "$1" || { echo "Bersaglio sconosciuto: $1 (vedi dispositivi.json)" >&2; exit 1; }
}

target_files() {  # target_files PRODOTTO, dal checkout corrente
  python3 - "$1" <<'PY'
from pathlib import Path
import sys
matches = list(Path('out/target/product').glob(
    f'*/obj/PACKAGING/target_files_intermediates/{sys.argv[1]}-target_files*.zip'))
if not matches:
    sys.exit(f'Manca target-files per {sys.argv[1]}: compila quel prodotto o indica AIOS_TARGET_FILES.')
print(max(matches, key=lambda p: p.stat().st_mtime))
PY
}

copia_in_sorgenti() {  # copia_in_sorgenti ORIGINE DESTINAZIONE
  # Soong non entra nelle cartelle collegate con link simbolici (build/soong/finder,
  # salvo ALLOW_BP_UNDER_SYMLINKS): prodotti e Android.bp vanno copiati nei sorgenti.
  # Ogni chiamata aggiorna la copia; il segno .soia-copia la distingue da file altrui.
  local origine="$1" destinazione="$2"
  command -v rsync >/dev/null || { echo "Manca rsync" >&2; exit 1; }
  [ -d "$origine" ] || { echo "Manca $origine" >&2; exit 1; }
  if [ -L "$destinazione" ]; then
    rm "$destinazione"  # collegamento delle versioni precedenti
  elif [ -e "$destinazione" ] && [ ! -e "$destinazione/.soia-copia" ]; then
    echo "$destinazione esiste e non è una copia di SoIA: fermo senza sovrascriverla." >&2; exit 1
  fi
  mkdir -p "$destinazione"
  rsync -a --delete --exclude=/.git --exclude=/.soia-copia "$origine/" "$destinazione/"
  touch "$destinazione/.soia-copia"
}

collega_soia() {  # collega_soia CARTELLA_SORGENTI: SoIA aggiornato dentro i sorgenti Android
  copia_in_sorgenti "$PHONE/device/aios" "$1/device/aios"
  copia_in_sorgenti "$PHONE/vendor/aios" "$1/vendor/aios"
  copia_in_sorgenti "$PHONE/packages/apps/Nova" "$1/packages/apps/Nova"
}
