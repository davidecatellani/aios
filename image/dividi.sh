#!/bin/bash
# Divide un file grande in pezzi da 1900 MB (GitHub accetta file fino a 2 GB): nome.parte0, nome.parte1…
# Come «split -b 1900M -d -a 1», ma dalla fine e accorciando il file man mano: sul disco non serve mai lo
# spazio per due copie intere (la ISO e il pacchetto di aggiornamento sono di 15 GB l'uno).
set -euo pipefail
trap 'echo "dividi.sh: fermo alla riga $LINENO: $BASH_COMMAND (codice $?)"' ERR
file="$1"
pezzo=$((${PEZZO_MB:-1900} * 1024 * 1024))
dimensione=$(stat -c %s "$file")
n=$(( (dimensione + pezzo - 1) / pezzo ))
df -h "$(dirname "$file")" | tail -n 1
for ((k = n - 1; k >= 0; k--)); do
  tail -c +$((k * pezzo + 1)) "$file" > "$file.parte$k"
  truncate -s $((k * pezzo)) "$file"
done
rm "$file"
