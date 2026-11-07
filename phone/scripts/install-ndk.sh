#!/usr/bin/env bash
# NDK r28c: checksum pubblicato nei metadati ufficiali Google repository2-3.xml.
source "$(dirname "$0")/comune.sh"
ROOT="${AIOS_NDK_ROOT:-$HOME/android-sdk}"
DEST="$ROOT/android-ndk-r28c"
mkdir -p "$ROOT"
exec 8>"$ROOT/.ndk-install.lock"
flock 8
if [ -d "$DEST" ]; then
  grep -Eq '^Pkg.Revision[[:space:]]*=[[:space:]]*28\.2\.13676358[[:space:]]*$' "$DEST/source.properties" \
    && [ -f "$DEST/build/cmake/android.toolchain.cmake" ] \
    || { echo "NDK esistente non valido: $DEST; non lo sovrascrivo." >&2; exit 1; }
  echo "$DEST"
  exit 0
fi
TEMP="$(mktemp -d "$ROOT/.ndk-download.XXXXXX")"
trap 'rm -rf "$TEMP"' EXIT
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
  https://dl.google.com/android/repository/android-ndk-r28c-linux.zip -o "$TEMP/ndk.zip"
printf '%s  %s\n' a7b54a5de87fecd125a17d54f73c446199e72a64 "$TEMP/ndk.zip" | sha1sum --check --status
unzip -q "$TEMP/ndk.zip" -d "$TEMP"
grep -Eq '^Pkg.Revision[[:space:]]*=[[:space:]]*28\.2\.13676358[[:space:]]*$' "$TEMP/android-ndk-r28c/source.properties"
test -f "$TEMP/android-ndk-r28c/build/cmake/android.toolchain.cmake"
mv "$TEMP/android-ndk-r28c" "$DEST"
echo "$DEST"
