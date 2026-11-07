#!/usr/bin/env bash
# llama.cpp per Android (arm64), con l'NDK: il server che Nova avvia solo quando serve.
source "$(dirname "$0")/comune.sh"
NDK="${ANDROID_NDK_HOME:?imposta ANDROID_NDK_HOME (NDK r27 o più recente)}"
LLAMA="${LLAMA_CPP:-$HOME/llama.cpp}"
[ -d "$LLAMA" ] || git clone --depth 1 --branch b11392 https://github.com/ggml-org/llama.cpp "$LLAMA"
[ "$(git -C "$LLAMA" rev-parse HEAD)" = 46847e61582097979f539595d893d83d8e1d1af1 ] \
  || { echo "llama.cpp deve essere b11392; non modifico il checkout esistente: $LLAMA" >&2; exit 1; }
cmake -S "$LLAMA" -B "$LLAMA/build-android" \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-30 -DCMAKE_BUILD_TYPE=Release \
  -DANDROID_STL=c++_static -DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON -DGGML_NATIVE=OFF \
  -DGGML_OPENMP=OFF -DLLAMA_OPENSSL=OFF -DBUILD_SHARED_LIBS=OFF \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_APP=OFF \
  -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF
cmake --build "$LLAMA/build-android" --target llama-server -j"$BUILD_JOBS"
mkdir -p "$PHONE/packages/apps/Nova/native/arm64-v8a"
# Un eseguibile di sistema: Soong lo installa in system_ext/bin con i permessi adatti.
cp "$LLAMA/build-android/bin/llama-server" "$PHONE/packages/apps/Nova/native/arm64-v8a/aios-llama-server"
echo "Fatto: packages/apps/Nova/native/arm64-v8a/aios-llama-server"
