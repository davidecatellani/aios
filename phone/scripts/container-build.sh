#!/usr/bin/env bash
# Eseguito dentro il contenitore; download preparatori e compilazione sono separati.
set -euo pipefail
cd /work/aios
case "${1:-}" in
  prototype)
    bash "$0" prepare
    bash "$0" build
    ;;
  prepare)
    # Identità tecnica locale per repo; nessuna credenziale GitHub viene richiesta.
    git config --global user.name 'SoIA Android builder'
    git config --global user.email 'builder@localhost'
    bash phone/scripts/prepara.sh gsi
    bash phone/scripts/install-ndk.sh
    bash phone/scripts/google-android.sh
    python3 phone/scripts/prepara-prototipo.py --cache "$AIOS_TOOLS/prototype-bundle"
    ;;
  build)
    test -f "$AIOS_SORGENTI/aosp-android-16.0.0_r3/build/envsetup.sh" \
      || { echo "Esegui prima pc-build.sh prepare." >&2; exit 1; }
    export ANDROID_NDK_HOME
    ANDROID_NDK_HOME="$(bash phone/scripts/install-ndk.sh)"
    bash phone/scripts/llama-android.sh
    bash phone/scripts/voce-android.sh
    bash phone/scripts/google-android.sh
    bash phone/scripts/compila.sh gsi
    ;;
  *) echo "Azione non valida: prepare|build|prototype" >&2; exit 2 ;;
esac
