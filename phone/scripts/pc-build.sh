#!/usr/bin/env bash
# Compilazione locale su Fedora/SoIA tramite Podman rootless. Non accede al telefono.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ACTION="${1:-check}"
case "$ACTION" in check|prepare|build|prototype) ;; *) echo "Uso: pc-build.sh check|prepare|build|prototype" >&2; exit 2 ;; esac
if [ "$(uname -m)" != x86_64 ]; then echo "La build richiede un PC Linux x86_64." >&2; exit 1; fi
if [ "$(id -u)" = 0 ]; then echo "Esegui come utente normale: Podman rootless, senza sudo." >&2; exit 1; fi
for tool in podman python3 flock; do
  command -v "$tool" >/dev/null || { echo "Manca $tool sul PC." >&2; exit 1; }
done
STATE="${AIOS_PC_BUILD_DIR:-$HOME/soia-phone-build}"
[[ "$STATE" = /* ]] || { echo "AIOS_PC_BUILD_DIR deve essere un percorso assoluto." >&2; exit 1; }
for path in "$REPO" "$STATE"; do
  [[ "$path" != *:* && "$path" != *$'\n'* ]] || { echo "Percorso non supportato: contiene ':' o un ritorno a capo." >&2; exit 1; }
done
mkdir -p "$STATE"
STATE="$(cd "$STATE" && pwd -P)"
[[ "$STATE" != / && "$STATE" != "$HOME" ]] || { echo "Usa una cartella dedicata, non la home o la radice." >&2; exit 1; }
case "$STATE/" in "$REPO/"*) echo "La cartella di build deve restare fuori dal repository." >&2; exit 1 ;; esac
case "$REPO/" in "$STATE/"*) echo "La cartella di build non deve contenere il repository." >&2; exit 1 ;; esac
exec 9>"$STATE/.build.lock"
flock -n 9 || { echo "Un'altra build usa già $STATE." >&2; exit 1; }
JOBS="${AIOS_BUILD_JOBS:-4}"
SYNC_JOBS="${AIOS_SYNC_JOBS:-4}"
[[ "$JOBS" =~ ^[1-9][0-9]*$ && "$SYNC_JOBS" =~ ^[1-9][0-9]*$ ]] || { echo "Il parallelismo deve essere un intero positivo." >&2; exit 1; }
SESSION_HOURS="${AIOS_SESSION_HOURS:-8}"
[[ "$SESSION_HOURS" =~ ^([0-9]|1[0-9]|2[0-4])$ ]] || { echo "AIOS_SESSION_HOURS deve essere un intero da 0 a 24 (0: senza pausa)." >&2; exit 1; }

# Report senza valori dell'ambiente, credenziali o identificativi del telefono.
python3 - "$STATE" "$ACTION" <<'PY'
from pathlib import Path
import shutil, sys
memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
ram = int(memory['MemTotal'].split()[0]) / 1024**2
swap = int(memory['SwapTotal'].split()[0]) / 1024**2
free = shutil.disk_usage(sys.argv[1]).free / 1024**3
print(f'RAM: {ram:.1f} GiB; swap: {swap:.1f} GiB; disco libero: {free:.1f} GiB')
sources = Path(sys.argv[1]) / 'sources/aosp-android-16.0.0_r3/.repo'
required = 30 if sources.is_dir() else 300
if sys.argv[2] != 'check' and free < required:
    sys.exit(f'Servono almeno {required} GiB liberi prima di procedere.')
PY
echo "Parallelismo build: $JOBS; sincronizzazione: $SYNC_JOBS"
podman info --format '{{.Host.Arch}}'
if [ "$ACTION" = check ]; then exit 0; fi

mkdir -p "$STATE/home" "$STATE/sources" "$STATE/tools"
RUN_ID="${GITHUB_RUN_ID:-local-$$}-${GITHUB_RUN_ATTEMPT:-1}"
[[ "$RUN_ID" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "Identificativo esecuzione non valido." >&2; exit 1; }
LOGS="$STATE/logs/$RUN_ID"
mkdir -p "$LOGS"
IMAGE=localhost/soia-android-builder:ubuntu24.04
set -o pipefail
podman build -t "$IMAGE" -f "$REPO/phone/build/Containerfile" "$REPO/phone/build" 2>&1 | tee "$LOGS/container.log"
CONTAINER="soia-gsi-$RUN_ID"
# La cancellazione del job/terminale deve fermare anche il contenitore.
stop_container() { podman stop --time 110 "$CONTAINER" >/dev/null 2>&1 || true; }
trap 'stop_container; exit 130' INT
trap 'stop_container; exit 143' TERM
echo "Sessione di download/compilazione: $SESSION_HOURS ore (0: senza pausa); poi circa due minuti per chiudere i processi."
# Solo directory della build; niente socket Docker, privilegi, USB o chiavi di firma.
if podman run --rm --name "$CONTAINER" --userns=keep-id --user "$(id -u):$(id -g)" \
  --ulimit nofile=65536:65536 \
  -v "$REPO:/work/aios:Z" -v "$STATE:/work/state:Z" \
  -e HOME=/work/state/home -e AIOS_SORGENTI=/work/state/sources \
  -e AIOS_NDK_ROOT=/work/state/tools -e LLAMA_CPP=/work/state/tools/llama.cpp \
  -e AIOS_TOOLS=/work/state/tools \
  -e "AIOS_BUILD_JOBS=$JOBS" -e "AIOS_SYNC_JOBS=$SYNC_JOBS" \
  "$IMAGE" python3 /work/aios/phone/scripts/build-session.py \
  --seconds "$((SESSION_HOURS * 3600))" --result "/work/state/logs/$RUN_ID/session.json" \
  -- bash /work/aios/phone/scripts/container-build.sh "$ACTION" \
  2>&1 | tee "$LOGS/build.log"; then
  echo "Fase $ACTION completata."
else
  STATUS=$?
  if [ "$STATUS" = 75 ] && python3 - "$LOGS/session.json" <<'PY'
import json, sys
try:
    doc = json.load(open(sys.argv[1]))
    sys.exit(0 if doc.get('state') == 'paused' and doc.get('reason') == 'time_limit' and doc.get('exit_code') == 75 else 1)
except (OSError, ValueError):
    sys.exit(1)
PY
  then
    MESSAGE="Pausa programmata: la fase $ACTION non è ancora completata. Puoi spegnere normalmente il PC e rilanciare la stessa fase alla prossima sessione; sorgenti e compilazione restano sul disco."
    echo "$MESSAGE"
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then printf '%s\n' "$MESSAGE" >> "$GITHUB_STEP_SUMMARY"; fi
    exit 0
  fi
  exit "$STATUS"
fi
