#!/usr/bin/env bash
# Compila l'immagine (non firmata: per la firma, scripts/firma.sh).
#   scripts/compila.sh miatoll | gsi | shiba
source "$(dirname "$0")/comune.sh"
B="${1:?uso: compila.sh BERSAGLIO}"
bersaglio_valido "$B"
BASE="$(campo "$B" base)"
cd "$SORGENTI/$BASE-$(campo "$B" ramo)"
[ -f "$PHONE/packages/apps/Nova/jni/arm64-v8a/libaios_llama_server.so" ] || {
  echo "Manca llama.cpp per Android: lancia prima scripts/llama-android.sh" >&2; exit 1; }
source build/envsetup.sh
if [ "$BASE" = "lineage" ]; then
  brunch "$(campo "$B" dispositivo_lineage)"
else
  lunch "$(campo "$B" prodotto)-$(campo "$B" rilascio)-user"
  m target-files-package
fi
echo "Compilato. Ora: scripts/firma.sh $B"
