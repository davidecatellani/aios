#!/usr/bin/env bash
# Crea le chiavi di SoIA (una volta sola, poi custodirle offline con una copia di sicurezza).
# Chi ha queste chiavi può firmare aggiornamenti per tutti i telefoni SoIA: mai nel repository.
source "$(dirname "$0")/comune.sh"
SRC="${1:?uso: chiavi.sh CARTELLA_SORGENTI (per development/tools/make_key)}"
mkdir -p "$CHIAVI" && chmod 700 "$CHIAVI"
SOGGETTO="/C=IT/O=AIOS/OU=AIOS/CN=AIOS"
for k in releasekey platform shared media networkstack sdk_sandbox bluetooth nfc; do
  # make_key chiede una password: vuota qui, la protezione è la cartella offline (o cifrata)
  [ -f "$CHIAVI/$k.pk8" ] || "$SRC/development/tools/make_key" "$CHIAVI/$k" "$SOGGETTO" <<< ""
done
# Chiave di avvio verificato (AVB): per i Pixel diventa avb_custom_key e permette di richiudere il bootloader
[ -f "$CHIAVI/avb.pem" ] || openssl genrsa -out "$CHIAVI/avb.pem" 4096
python3 "$SRC/external/avb/avbtool.py" extract_public_key --key "$CHIAVI/avb.pem" --output "$CHIAVI/avb_pkmd.bin"
# Chiave del catalogo (Ed25519): la stessa famiglia di firme dei modelli AI e dei temi
[ -f "$CHIAVI/catalogo.pem" ] || openssl genpkey -algorithm ed25519 -out "$CHIAVI/catalogo.pem"
chmod 600 "$CHIAVI"/*
echo "Chiavi in $CHIAVI. Chiave pubblica del catalogo (da mettere in /etc/aios/catalog-keys.d/ sui PC):"
openssl pkey -in "$CHIAVI/catalogo.pem" -pubout -outform DER | tail -c 32 | base64
