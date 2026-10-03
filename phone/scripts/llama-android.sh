#!/usr/bin/env bash
# llama.cpp per Android (arm64), con l'NDK: il server che Nova avvia solo quando serve.
source "$(dirname "$0")/comune.sh"
NDK="${ANDROID_NDK_HOME:?imposta ANDROID_NDK_HOME (NDK r27 o più recente)}"
LLAMA="${LLAMA_CPP:-$HOME/llama.cpp}"
[ -d "$LLAMA" ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA"
cmake -S "$LLAMA" -B "$LLAMA/build-android" \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-30 -DCMAKE_BUILD_TYPE=Release \
  -DGGML_OPENMP=OFF -DLLAMA_CURL=OFF -DBUILD_SHARED_LIBS=OFF
cmake --build "$LLAMA/build-android" --target llama-server -j"$(nproc)"
mkdir -p "$PHONE/packages/apps/Nova/jni/arm64-v8a"
# nome «lib….so»: Android lo estrae nella cartella delle librerie dell'app, da cui si può eseguire
cp "$LLAMA/build-android/bin/llama-server" "$PHONE/packages/apps/Nova/jni/arm64-v8a/libaios_llama_server.so"
echo "Fatto: packages/apps/Nova/jni/arm64-v8a/libaios_llama_server.so"
