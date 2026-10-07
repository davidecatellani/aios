#!/usr/bin/env bash
# Scarica i sorgenti della base (AOSP o LineageOS) e ci collega SoIA.
#   scripts/prepara.sh miatoll | gsi | shiba
source "$(dirname "$0")/comune.sh"
B="${1:?uso: prepara.sh BERSAGLIO}"
bersaglio_valido "$B"
BASE="$(campo "$B" base)"
DIR="$SORGENTI/$BASE-$(campo "$B" ramo)"

# Fermati prima di scaricare centinaia di GB o creare un checkout parziale.
python3 - "$DIR" <<'PY'
from pathlib import Path
import shutil
import sys
path = Path(sys.argv[1]).expanduser()
while not path.exists():
    path = path.parent
free = shutil.disk_usage(path).free / 1024**3
required = 30 if (Path(sys.argv[1]) / '.repo').is_dir() else 300
if free < required:
    sys.exit(f"Spazio insufficiente: {free:.1f} GiB liberi; servono almeno {required} GiB per AOSP/LineageOS.")
PY

for cmd in repo git python3; do command -v "$cmd" >/dev/null || { echo "Manca $cmd" >&2; exit 1; }; done
mkdir -p "$DIR" && cd "$DIR"
if [ ! -d .repo ]; then
  repo init -u "$(campo "$B" manifest)" -b "$(campo "$B" ramo)" --repo-rev=v2.54 --git-lfs --no-clone-bundle
fi
repo sync -c -j"${AIOS_SYNC_JOBS:-4}" --no-tags --optimized-fetch

# SoIA si aggiunge senza modificare la base: collegamenti a questa cartella del repository
mkdir -p vendor packages/apps
mkdir -p device
ln -sfn "$PHONE/device/aios" device/aios
ln -sfn "$PHONE/vendor/aios" vendor/aios
ln -sfn "$PHONE/packages/apps/Nova" packages/apps/Nova
if [ "$BASE" = "lineage" ]; then
  mkdir -p vendor/extra
  ln -sfn "$PHONE/vendor/aios/extra-product.mk" vendor/extra/product.mk  # LineageOS lo include da sé
  set +u  # envsetup legge variabili opzionali non definite in una shell pulita
  source build/envsetup.sh
  breakfast "$(campo "$B" dispositivo_lineage)"   # scarica i file hardware del telefono (roomservice)
  set -u
  echo "Ricorda gli eventuali file proprietari: vedi la guida di compilazione di LineageOS per $(campo "$B" dispositivo_lineage)."
fi
echo "Pronto in $DIR. Poi: scripts/compila.sh $B"
