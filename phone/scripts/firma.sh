#!/usr/bin/env bash
# Firma l'immagine con le chiavi di SoIA e prepara i file per il catalogo.
#   scripts/firma.sh miatoll | gsi | shiba
source "$(dirname "$0")/comune.sh"
B="${1:?uso: firma.sh BERSAGLIO}"
bersaglio_valido "$B"
BASE="$(campo "$B" base)"
cd "$SORGENTI/$BASE-$(campo "$B" ramo)"
USCITA="$PHONE/uscita/$B"
mkdir -p "$USCITA"
TF="$(ls -t out/target/product/*/obj/PACKAGING/target_files_intermediates/*-target_files*.zip | head -1)"
FIRMATI="$USCITA/target_files-firmati.zip"
python3 build/tools/releasetools/sign_target_files_apks.py -o -d "$CHIAVI" \
  --avb_vbmeta_key "$CHIAVI/avb.pem" --avb_vbmeta_algorithm SHA256_RSA4096 "$TF" "$FIRMATI"
case "$B" in
  gsi)
    unzip -o -j "$FIRMATI" IMAGES/system.img IMAGES/vbmeta.img -d "$USCITA" ;;
  miatoll)
    python3 build/tools/releasetools/ota_from_target_files.py -k "$CHIAVI/releasekey" "$FIRMATI" "$USCITA/aios-miatoll.zip"
    unzip -o -j "$FIRMATI" IMAGES/recovery.img -d "$USCITA" || unzip -o -j "$FIRMATI" IMAGES/boot.img -d "$USCITA" ;;
  *)
    python3 build/tools/releasetools/img_from_target_files.py "$FIRMATI" "$USCITA/aios-$B-img.zip"
    cp "$CHIAVI/avb_pkmd.bin" "$USCITA/" ;;
esac
echo "Firmato in $USCITA. Ora il catalogo: aios-catalogo-telefoni --bersaglio $B --cartella $USCITA --url https://…"
