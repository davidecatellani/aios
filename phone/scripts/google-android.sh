#!/usr/bin/env bash
# Integrazione inline MindTheGapps Android 16 ARM64, senza alterare le firme Google.
source "$(dirname "$0")/comune.sh"
TOOLS="${AIOS_TOOLS:-$HOME/.cache/aios-tools}"
GAPPS="${AIOS_GAPPS_SOURCE:-$TOOLS/vendor-gapps-16}"
REVISION=39bd3a09640efb81234cdbd8ab98ab71541d5d46
mkdir -p "$TOOLS"
if [ ! -d "$GAPPS/.git" ]; then
  git clone --depth 1 --branch baklava https://github.com/MindTheGapps/vendor_gapps.git "$GAPPS"
fi
if [ "$(git -C "$GAPPS" rev-parse HEAD)" != "$REVISION" ]; then
  git -C "$GAPPS" fetch --depth 1 origin "$REVISION"
  git -C "$GAPPS" checkout --detach "$REVISION"
fi
test "$(git -C "$GAPPS" rev-parse HEAD)" = "$REVISION"
git -C "$GAPPS" diff --quiet && git -C "$GAPPS" diff --cached --quiet \
  || { echo 'Sorgenti Google modificati: usa un checkout pulito.' >&2; exit 1; }
python3 - "$GAPPS" <<'PY'
from pathlib import Path
import re, sys, zipfile
root = Path(sys.argv[1])
# Le overlay upstream sono esportate da common-vendor.mk, ma non dichiarano
# il namespace Soong: aggiungilo senza alterare APK o file tracciati upstream.
namespace = root / 'overlay/Android.bp'
expected = '// Generato da SoIA per la build inline AOSP.\nsoong_namespace {}\n'
if namespace.exists() and namespace.read_text() != expected:
    sys.exit('Namespace Google inatteso: fermo senza sovrascriverlo')
namespace.write_text(expected)
for directory in ('arm64', 'common'):
    build = (root / directory / 'Android.bp').read_text()
    paths = re.findall(r'"(proprietary/[^"\n]+)"', build)
    if not paths:
        sys.exit('Nessun file proprietario Google nel manifest')
    for name in paths:
        path = root / directory / name
        if not path.is_file() or not path.stat().st_size:
            sys.exit(f'Pacchetto Google incompleto: {name}')
        if path.suffix == '.apk':
            with zipfile.ZipFile(path) as apk:
                if 'AndroidManifest.xml' not in apk.namelist():
                    sys.exit(f'APK Google non valido: {name}')
print('Google ARM64: revisione e contenuto verificati; firme APK preservate.')
PY
DIR="$SORGENTI/aosp-android-16.0.0_r3"
mkdir -p "$DIR/vendor"
if [ -e "$DIR/vendor/gapps" ] && [ ! -L "$DIR/vendor/gapps" ]; then
  echo 'vendor/gapps è già presente e non è un link: fermo senza sovrascriverlo.' >&2; exit 1
fi
ln -sfn "$GAPPS" "$DIR/vendor/gapps"
