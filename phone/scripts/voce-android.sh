#!/usr/bin/env bash
# Riconoscimento e sintesi offline; dati generati sul PC, binari Android ARM64.
set -euo pipefail
PHONE="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="${AIOS_TOOLS:-$HOME/.cache/aios-tools}"
NDK="${ANDROID_NDK_HOME:?Imposta ANDROID_NDK_HOME}"
JOBS="${AIOS_BUILD_JOBS:-4}"
mkdir -p "$TOOLS" "$PHONE/packages/apps/Nova/native/arm64-v8a" "$PHONE/packages/apps/Nova/bundle"
source_repo() {
  local directory="$1" url="$2" tag="$3" revision="$4"
  if [ ! -d "$directory/.git" ]; then git clone --depth 1 --branch "$tag" "$url" "$directory"; fi
  test "$(git -C "$directory" rev-parse HEAD)" = "$revision" || { echo "Revisione inattesa: $directory" >&2; exit 1; }
}
WHISPER="${WHISPER_CPP:-$TOOLS/whisper-android}"
ESPEAK="${ESPEAK_NG:-$TOOLS/espeak-android}"
source_repo "$WHISPER" https://github.com/ggml-org/whisper.cpp.git v1.8.2 4979e04f5dcaccb36057e059bbaed8a2f5288315
source_repo "$ESPEAK" https://github.com/espeak-ng/espeak-ng.git 1.52.0 4870adfa25b1a32b4361592f1be8a40337c58d6c
android=("-DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake" -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-29 -DANDROID_STL=c++_static -DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXE_LINKER_FLAGS=-Wl,-z,max-page-size=16384)
cmake -S "$WHISPER" -B "$WHISPER/build-aios" "${android[@]}" -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF -DWHISPER_BUILD_TESTS=OFF -DWHISPER_CURL=OFF -DWHISPER_SDL2=OFF
cmake --build "$WHISPER/build-aios" --target whisper-cli -j "$JOBS"
cp "$WHISPER/build-aios/bin/whisper-cli" "$PHONE/packages/apps/Nova/native/arm64-v8a/aios-whisper"
# Evita il FetchContent di sonic: la sintesi produce un WAV, senza audio/libsonic.
options=(-DBUILD_SHARED_LIBS=OFF -DUSE_LIBSONIC=OFF -DUSE_LIBPCAUDIO=OFF -DUSE_MBROLA=OFF -DUSE_ASYNC=OFF "-DSONIC_LIB=$ESPEAK/CMakeLists.txt" "-DSONIC_INC=$ESPEAK/src")
cmake -S "$ESPEAK" -B "$ESPEAK/build-host-aios" -DCMAKE_BUILD_TYPE=Release "${options[@]}"
cmake --build "$ESPEAK/build-host-aios" -j "$JOBS"
cmake -S "$ESPEAK" -B "$ESPEAK/build-arm-aios" "${android[@]}" "${options[@]}"
# Non eseguire i generatori ARM sul PC: i dati sono già compilati sopra.
cmake --build "$ESPEAK/build-arm-aios" --target espeak-ng-bin -j "$JOBS"
cp "$ESPEAK/build-arm-aios/src/espeak-ng" "$PHONE/packages/apps/Nova/native/arm64-v8a/aios-espeak"
python3 - "$ESPEAK/build-host-aios" "$PHONE/packages/apps/Nova/bundle" <<'PY'
import hashlib, json, sys, zipfile
from pathlib import Path
source, output = map(Path, sys.argv[1:])
target = output / 'voice-data.zip'
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted((source / 'espeak-ng-data').rglob('*')):
        if path.is_file():
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
manifest = output / 'manifest.json'
doc = json.loads(manifest.read_text()) if manifest.exists() else {'version': 1, 'artifacts': []}
doc['artifacts'] = [a for a in doc['artifacts'] if a['name'] != target.name]
doc['artifacts'].append({'name': target.name, 'kind': 'voice-data', 'size': target.stat().st_size,
    'sha256': hashlib.file_digest(target.open('rb'), 'sha256').hexdigest(), 'license': 'GPL-3.0-or-later',
    'source': 'https://github.com/espeak-ng/espeak-ng/tree/1.52.0'})
manifest.write_text(json.dumps(doc, indent=2))
PY
echo 'Voce offline ARM64 pronta.'
