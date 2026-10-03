#!/usr/bin/env bash
# Misura il consumo in standby di un telefono con AIOS (collegato via adb, schermo spento).
# Obiettivo: ro.aios.energia.standby_max_per_ora (1% all'ora). Da fare a ogni versione.
#   scripts/misura-batteria.sh [ore=2]
source "$(dirname "$0")/comune.sh"
ORE="${1:-2}"
# Con il cavo collegato il telefono si ricarica: la misura si fa a cavo scollegato.
adb shell dumpsys batterystats --reset >/dev/null
INIZIO="$(adb shell dumpsys battery | awk '/level:/ {print $2; exit}')"
OBIETTIVO="$(adb shell getprop ro.aios.energia.standby_max_per_ora | tr -d '\r')"
echo "Batteria al $INIZIO%. Spegni lo schermo, SCOLLEGA il cavo e lascia il telefono fermo per $ORE ore."
read -r -p "Dopo $ORE ore ricollega il cavo e premi Invio… " _
FINE="$(adb shell dumpsys battery | awk '/level:/ {print $2; exit}')"
echo "Chi ha tenuto sveglio il telefono:"
adb shell dumpsys batterystats | grep -iE "wake lock|wakeup alarm" | head -20 || true
python3 - "$INIZIO" "$FINE" "$ORE" "${OBIETTIVO:-1.0}" <<'PY'
import sys
a, b, h, goal = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
rate = (a - b) / h
print(f"Consumo in standby: {rate:.2f}% all'ora (obiettivo {goal}%).")
sys.exit(0 if rate <= goal else 1)
PY
