#!/usr/bin/env bash
# Scarica i sorgenti della base (AOSP o LineageOS) e ci collega SoIA.
#   scripts/prepara.sh miatoll | gsi | shiba
source "$(dirname "$0")/comune.sh"
B="${1:?uso: prepara.sh BERSAGLIO}"
bersaglio_valido "$B"
BASE="$(campo "$B" base)"
DIR="$SORGENTI/$BASE-$(campo "$B" ramo)"
mkdir -p "$DIR" && cd "$DIR"

for cmd in repo git python3; do command -v "$cmd" >/dev/null || { echo "Manca $cmd" >&2; exit 1; }; done
if [ ! -d .repo ]; then
  repo init -u "$(campo "$B" manifest)" -b "$(campo "$B" ramo)" --git-lfs --no-clone-bundle
fi
repo sync -c -j"$(nproc)" --no-tags --optimized-fetch

# SoIA si aggiunge senza modificare la base: collegamenti a questa cartella del repository
mkdir -p vendor packages/apps
ln -sfn "$PHONE/vendor/aios" vendor/aios
ln -sfn "$PHONE/packages/apps/Nova" packages/apps/Nova
if [ "$BASE" = "lineage" ]; then
  mkdir -p vendor/extra
  ln -sfn "$PHONE/vendor/aios/extra-product.mk" vendor/extra/product.mk  # LineageOS lo include da sé
  source build/envsetup.sh
  breakfast "$(campo "$B" dispositivo_lineage)"   # scarica i file hardware del telefono (roomservice)
  echo "Ricorda gli eventuali file proprietari: vedi la guida di compilazione di LineageOS per $(campo "$B" dispositivo_lineage)."
fi
echo "Pronto in $DIR. Poi: scripts/compila.sh $B"
