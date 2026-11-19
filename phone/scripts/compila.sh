#!/usr/bin/env bash
# Compila l'immagine (non firmata: per la firma, scripts/firma.sh).
#   scripts/compila.sh miatoll | gsi | shiba
source "$(dirname "$0")/comune.sh"
B="${1:?uso: compila.sh BERSAGLIO}"
bersaglio_valido "$B"
BASE="$(campo "$B" base)"
cd "$SORGENTI/$BASE-$(campo "$B" ramo)"
[ -f "$PHONE/packages/apps/Nova/native/arm64-v8a/aios-llama-server" ] || {
  echo "Manca llama.cpp per Android: lancia prima scripts/llama-android.sh" >&2; exit 1; }
# Copia aggiornata di SoIA nei sorgenti, con i motori nativi e i modelli appena preparati.
collega_soia "$PWD"
# envsetup AOSP/Lineage legge variabili opzionali (TOP, ZSH_VERSION, ecc.).
# Manteniamo errexit/pipefail, ma sospendiamo nounset durante i comandi upstream.
set +u
source build/envsetup.sh
if [ "$BASE" = "lineage" ]; then
  brunch "$(campo "$B" dispositivo_lineage)" "-j$BUILD_JOBS"
else
  lunch "$(campo "$B" prodotto)-$(campo "$B" rilascio)-user"
  # -k0: dopo un errore continua con tutto il resto e alla fine li elenca tutti, così un giro
  # di molte ore mostra ogni problema invece del primo.
  m -k0 "-j$BUILD_JOBS" target-files-package
fi
set -u
if [ "$B" = gsi ]; then
  TF="$(target_files "$(campo "$B" prodotto)")"
  python3 "$PHONE/scripts/esporta-prototipo.py" "$TF" "$PHONE/uscita/gsi/prototipo" --sdk "$(campo "$B" android_sdk)"
  echo "Target-files controllato: $PWD/$TF"
fi
echo "Compilato. Ora: scripts/firma.sh $B"
