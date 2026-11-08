#!/usr/bin/env bash
# repo sync con ripresa dei progetti mancanti. Si lancia dalla cartella dei sorgenti.
# Un progetto enorme (es. il compilatore platform/prebuilts/clang/host/linux-x86) che
# si interrompe per la rete («Connection reset by peer») viene riprovato da solo, uno
# alla volta, con tutta la connessione per sé; quello già scaricato non si riscarica.
set -euo pipefail
JOBS="${AIOS_SYNC_JOBS:-4}"
GIRI="${AIOS_SYNC_GIRI:-3}"
registro="$(mktemp)"
trap 'rm -f "$registro"' EXIT

sincronizza() { repo sync -c --no-tags --optimized-fetch --retry-fetches=4 "$@" 2>&1 | tee "$registro"; }

sincronizza -j"$JOBS" && exit 0
for giro in $(seq 1 "$GIRI"); do
  mapfile -t mancanti < <(sed -n 's/^error: Cannot fetch \([^ ]*\) .*/\1/p' "$registro" | sort -u)
  if [ "${#mancanti[@]}" = 0 ]; then
    echo "Download dei sorgenti non riuscito (vedi sopra). Rilancia: quello già scaricato resta." >&2
    exit 1
  fi
  echo "Mancano ${#mancanti[@]} progetti (${mancanti[*]}): li riprovo uno alla volta, giro $giro di $GIRI."
  if sincronizza -j1 "${mancanti[@]}"; then
    # Estrae i file di tutti i progetti, senza rete: nessuno resta a metà.
    repo sync --local-only -j"$JOBS"
    exit 0
  fi
done
echo "Download dei sorgenti non completato dopo $GIRI giri: rilancia più tardi, quello già scaricato resta." >&2
exit 1
