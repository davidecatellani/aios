#!/usr/bin/env bash
# Test JVM del protocollo, senza compilare AOSP. Dipendenze: Kotlin e org.json.
set -euo pipefail
PHONE="$(cd "$(dirname "$0")/.." && pwd)"
COMPILER="${AIOS_KOTLINC:-kotlinc}"
JSON="${AIOS_JSON_JAR:?Indica AIOS_JSON_JAR (org.json:json:20240303)}"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
"$COMPILER" \
  "$PHONE/packages/apps/Nova/src/org/aios/nova/pc/PcJob.kt" \
  "$PHONE/packages/apps/Nova/src/org/aios/nova/pc/PcDecision.kt" \
  "$PHONE/tests/PcJobTest.kt" \
  -classpath "$JSON" -include-runtime -d "$OUT/tests.jar"
java -cp "$OUT/tests.jar:$JSON" org.aios.nova.tests.PcJobTestKt

PYTHONPATH="$PHONE/../copilot" python3 "$PHONE/tests/interop.py" prepare "$OUT/fixture.json"
"$COMPILER" "$PHONE/packages/apps/Nova/src/org/aios/nova/core/Crypto.kt" \
  "$PHONE/packages/apps/Nova/src/org/aios/nova/core/VerifiedFiles.kt" \
  "$PHONE/packages/apps/Nova/src/org/aios/nova/core/SyncDocument.kt" "$PHONE/tests/CoreTest.kt" \
  -classpath "$JSON" -include-runtime -d "$OUT/core.jar"
java -cp "$OUT/core.jar:$JSON" org.aios.nova.tests.CoreTestKt "$OUT/fixture.json" "$OUT"
PYTHONPATH="$PHONE/../copilot" python3 "$PHONE/tests/interop.py" verify "$OUT"

# Facoltativo: controlla anche tutti i sorgenti dell'app contro le API framework Android.
# R è generato solo per risolvere gli identificativi: non sostituisce packaging o test su dispositivo.
if [ -n "${AIOS_ANDROID_JAR:-}" ]; then
  python3 - "$PHONE" "$OUT/R.kt" <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
root = Path(sys.argv[1]) / "packages/apps/Nova"
names = [e.attrib["name"] for e in ET.parse(root / "res/values/strings.xml").getroot()]
Path(sys.argv[2]).write_text("package org.aios.nova\nobject R { object string {\n" +
    "".join(f"const val {name} = {i}\n" for i, name in enumerate(names, 1)) + "} }\n")
PY
  "$COMPILER" "$PHONE/packages/apps/Nova/src" "$OUT/R.kt" \
    -classpath "$AIOS_ANDROID_JAR" -d "$OUT/nova.jar"
  echo "Tutti i sorgenti Kotlin compilati (non è una build APK o dell'immagine)."
fi
