#!/usr/bin/env bash
# SoIA per telefono, tutto con un comando sul PC Fedora/SoIA (dalla cartella del repository):
# aggiorna il codice, controlla lo spazio, legge il telefono se è collegato, compila a
# sessioni riprendibili e alla fine dice com'è andata. Non scrive mai sul telefono.
#
#   bash phone/compila-telefono.sh                    sessione di 8 ore, poi rilancialo
#   bash phone/compila-telefono.sh --ore 12           sessione più lunga (0: fino alla fine)
#   bash phone/compila-telefono.sh --senza-aggiornare non scaricare le ultime correzioni
#   bash phone/compila-telefono.sh --ignora-spazio    solo se sai che lo spazio basta
#   bash phone/compila-telefono.sh --ignora-memoria   solo se sai che la memoria basta
set -euo pipefail

SPAZIO_TOTALE_GIB=300   # sorgenti AOSP + compilazione (PC-BUILD.md)
MEMORIA_MINIMA_GIB=60   # RAM + swap: Google consiglia 64 GB per compilare Android

uso() { sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

NOVA_SEGNO=""   # segno «aios-gioco» creato da questo script
NOVA_NUCLEO=0   # nucleo di Nova fermato da questo script

# Mentre compila, Nova si fa da parte come per i giochi (copilot/aios_copilot/giochi.py): il segno
# «aios-gioco» mette in pausa i lavori a riposo, i modelli di Ollama escono dalla memoria (si
# ricaricano alla prima domanda) e il nucleo si ferma. ripristina_nova rimette tutto com'era.
libera_memoria_nova() {
  local segno="$1/aios-gioco"
  echo "== Nova si fa da parte durante la compilazione (come per i giochi)"
  if [ -d "$1" ] && [ ! -e "$segno" ] && touch "$segno" 2>/dev/null; then NOVA_SEGNO="$segno"; fi
  python3 - "${AIOS_OLLAMA_URL:-http://localhost:11434}" <<'PY' || true
import json, sys, urllib.request
base = sys.argv[1].rstrip("/")
try:
    with urllib.request.urlopen(base + "/api/ps", timeout=5) as risposta:
        modelli = [m["name"] for m in json.load(risposta).get("models", [])]
except (OSError, ValueError, KeyError):
    modelli = []
for nome in modelli:
    richiesta = urllib.request.Request(base + "/api/generate", headers={"Content-Type": "application/json"},
                                       data=json.dumps({"model": nome, "keep_alive": 0}).encode())
    try:
        urllib.request.urlopen(richiesta, timeout=60).read()
    except OSError:
        pass
if modelli:
    print("Modelli di Nova tolti dalla memoria:", ", ".join(modelli))
PY
  if systemctl is-active --quiet aios-nucleo.service 2>/dev/null \
      && systemctl stop --no-block aios-nucleo.service >/dev/null 2>&1; then
    NOVA_NUCLEO=1
    echo "Nucleo di Nova fermato fino alla fine della compilazione."
  fi
}

ripristina_nova() {
  if [ -n "$NOVA_SEGNO" ]; then rm -f "$NOVA_SEGNO"; NOVA_SEGNO=""; fi
  if [ "$NOVA_NUCLEO" = 1 ]; then
    systemctl start --no-block aios-nucleo.service >/dev/null 2>&1 || true
    NOVA_NUCLEO=0
  fi
}

main() {
  local repo ore="${AIOS_SESSION_HOURS:-8}" aggiorna=1 controlla_spazio=1 controlla_memoria=1 argomenti=("$@")
  repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  while [ $# -gt 0 ]; do
    case "$1" in
      --ore) [ $# -ge 2 ] || { echo "--ore vuole un numero di ore." >&2; return 2; }; ore="$2"; shift 2 ;;
      --senza-aggiornare) aggiorna=0; shift ;;
      --ignora-spazio) controlla_spazio=0; shift ;;
      --ignora-memoria) controlla_memoria=0; shift ;;
      -h|--help) uso; return 0 ;;
      *) echo "Opzione sconosciuta: $1" >&2; uso >&2; return 2 ;;
    esac
  done
  [[ "$ore" =~ ^([0-9]|1[0-9]|2[0-4])$ ]] || { echo "--ore: un intero da 0 a 24 (0: senza pausa)." >&2; return 2; }
  if [ "$(id -u)" = 0 ]; then echo "Lancialo come utente normale, senza sudo." >&2; return 1; fi

  # 1. Codice aggiornato: le correzioni arrivano da GitHub con un semplice pull.
  if [ "$aggiorna" = 1 ] && git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    local prima
    prima="$(git -C "$repo" rev-parse HEAD)"
    echo "== Aggiorno il codice"
    if GIT_TERMINAL_PROMPT=0 git -C "$repo" pull --ff-only; then
      if [ "$(git -C "$repo" rev-parse HEAD)" != "$prima" ]; then
        echo "Codice aggiornato: riparto con la nuova versione di questo script."
        exec bash "$repo/phone/compila-telefono.sh" --senza-aggiornare "${argomenti[@]}"
      fi
    else
      echo "ATTENZIONE: aggiornamento non riuscito, continuo con il codice già presente." >&2
    fi
  fi

  # 2. Registro di tutto quello che segue, da mandare se qualcosa va storto.
  local stato="${AIOS_PC_BUILD_DIR:-$HOME/soia-phone-build}" log
  [[ "$stato" = /* ]] || { echo "AIOS_PC_BUILD_DIR deve essere un percorso assoluto." >&2; return 1; }
  mkdir -p "$stato/logs"
  log="$stato/logs/compila-telefono-$(date +%Y%m%d-%H%M%S).log"
  # tee ignora Ctrl+C: il messaggio finale arriva anche nel registro.
  exec > >(trap '' INT TERM; exec tee -a "$log") 2>&1
  echo "== SoIA per telefono — $(date '+%d/%m/%Y %H:%M')"
  echo "Registro: $log"
  if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    local ramo
    ramo="$(git -C "$repo" branch --show-current)"
    echo "Codice: ramo ${ramo:-nessuno}, versione $(git -C "$repo" rev-parse --short HEAD)"
  fi

  # 3. Spazio: dopo l'inizio del download pc-build.sh chiede solo 30 GiB di margine,
  #    qui si conta anche quello che i sorgenti occupano già.
  local sorgenti="$stato/sources/aosp-android-16.0.0_r3" libero usato_kib usato
  libero=$(( $(df -Pk "$stato" | awk 'NR==2 {print $4}') / 1048576 ))
  echo "== Spazio: $libero GiB liberi"
  if [ "$controlla_spazio" = 1 ] && [ "$libero" -lt "$SPAZIO_TOTALE_GIB" ]; then
    if [ ! -d "$sorgenti/.repo" ]; then
      echo "Servono almeno $SPAZIO_TOTALE_GIB GiB liberi: liberane altri $((SPAZIO_TOTALE_GIB - libero)) e rilancia." >&2
      return 1
    fi
    echo "Calcolo lo spazio già occupato dai sorgenti (può richiedere qualche minuto)…"
    usato_kib="$(du -skx "$sorgenti" 2>/dev/null | awk '{print $1}' || true)"
    usato=$(( ${usato_kib:-0} / 1048576 ))
    echo "Sorgenti e compilazione già sul disco: $usato GiB"
    if [ $((usato + libero)) -lt "$SPAZIO_TOTALE_GIB" ]; then
      echo "Per finire servono circa $SPAZIO_TOTALE_GIB GiB in tutto: liberane altri $((SPAZIO_TOTALE_GIB - usato - libero)) e rilancia." >&2
      echo "Se sei sicuro che lo spazio basti: bash phone/compila-telefono.sh --ignora-spazio" >&2
      return 1
    fi
  fi

  # Podman tiene lo stato dei contenitori in /run/user/<utente>, un'area piccola in memoria:
  # se è piena il contenitore non parte («No space left on device» di crun).
  local runtime="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" libero_run
  if [ -d "$runtime" ]; then
    libero_run=$(( $(df -Pk "$runtime" | awk 'NR==2 {print $4}') / 1024 ))
    if [ "$libero_run" -lt 64 ]; then
      echo "La memoria temporanea dell'utente ($runtime) è piena: restano $libero_run MB." >&2
      echo "Occupano di più:" >&2
      { du -sh "$runtime"/* 2>/dev/null | sort -h | tail -5 >&2; } || true
      echo "Riavvia il PC (quell'area si svuota da sola; i sorgenti scaricati restano) e rilancia." >&2
      return 1
    fi
  fi

  # Memoria: l'analisi dei file di Android 16 (soong_build) da sola supera i 30 GB. Con meno
  # RAM + swap il sistema la chiude («Killed»): serve uno swap su disco (lo zram di Fedora,
  # compresso in RAM, aggiunge poco).
  local memoria_gib
  memoria_gib=$(( $(free -m | awk '/^Mem:/ {m=$2} /^Swap:/ {s=$2} END {print m + s}') / 1024 ))
  echo "== Memoria: $memoria_gib GiB tra RAM e swap"
  if [ "$controlla_memoria" = 1 ] && [ "$memoria_gib" -lt "$MEMORIA_MINIMA_GIB" ]; then
    echo "Per compilare Android 16 servono almeno $MEMORIA_MINIMA_GIB GiB tra RAM e swap: l'analisi dei" >&2
    echo "file di Android ne usa più di 30 e con meno il sistema la chiude («Killed»)." >&2
    echo "Aggiungi (o riattiva dopo un riavvio) uno swap su disco da 32 GB:" >&2
    if [ ! -f /var/swapfile ]; then
      if [ "$(stat -f -c %T /var 2>/dev/null)" = btrfs ]; then
        echo "  sudo btrfs filesystem mkswapfile --size 32g /var/swapfile" >&2
      else
        echo "  sudo fallocate -l 32G /var/swapfile && sudo chmod 600 /var/swapfile && sudo mkswap /var/swapfile" >&2
      fi
    fi
    echo "  sudo swapon /var/swapfile" >&2
    echo "Poi rilancia. Per provare lo stesso: bash phone/compila-telefono.sh --ignora-memoria" >&2
    return 1
  fi

  # 4. Telefono: solo lettura di alcune proprietà, se adb c'è e il Motorola è collegato.
  echo "== Telefono"
  if command -v adb >/dev/null 2>&1; then
    local rapporto="$repo/phone/uscita/verifica-telefono.json"
    mkdir -p "$repo/phone/uscita"
    adb start-server </dev/null >/dev/null 2>&1 || true
    if python3 "$repo/phone/scripts/verifica-telefono.py" >"$rapporto.tmp" 2>"$rapporto.err"; then
      mv "$rapporto.tmp" "$rapporto"
      python3 - "$rapporto" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
p = r["proprieta"]
print(f"Modello: {p['ro.product.model']} — Android {p['ro.build.version.release']} (SDK {p['ro.build.version.sdk']})")
print("Bootloader sbloccato:", {True: "sì", False: "no", None: "non accertato"}[r["bootloader"]["sbloccato"]])
print("Requisiti della GSI:", "sì" if r["prerequisiti_gsi_rilevati"] else "no")
for problema in r["blocchi_installazione"]:
    print(" -", problema)
PY
      echo "Rapporto completo (senza numero di serie): $rapporto"
    else
      cat "$rapporto.err"
      echo "Controllo del telefono saltato: non blocca la compilazione."
    fi
    rm -f "$rapporto.tmp" "$rapporto.err"
  else
    echo "adb non è installato: controllo del telefono saltato (si può fare anche dal portatile)."
  fi

  # 5. Il PC non deve andare in sospensione durante la sessione.
  local inibisci=()
  if command -v systemd-inhibit >/dev/null 2>&1 \
      && systemd-inhibit --what=sleep:idle --who=SoIA --why="prova" true >/dev/null 2>&1; then
    inibisci=(systemd-inhibit --what=sleep:idle --who=SoIA --why="Compilazione di SoIA per telefono")
    echo "Il PC resterà acceso finché la sessione è in corso."
  else
    echo "ATTENZIONE: non riesco a impedire la sospensione: disattivala nelle impostazioni del PC."
  fi

  # Nova lascia memoria e processore alla compilazione; alla fine, anche dopo un errore o
  # la chiusura del terminale, torna com'era.
  trap ripristina_nova EXIT
  trap 'exit 129' HUP
  trap 'exit 143' TERM
  libera_memoria_nova "$runtime"

  # 6. Compilazione: prepara + compila + controlla + estrae le immagini.
  echo "== Compilazione (sessione di $ore ore; 0 = fino alla fine)"
  echo "Lascia aperto questo terminale. Ctrl+C ferma tutto: il lavoro fatto resta sul disco."
  local inizio codice=0
  inizio="$(date +%s)"
  AIOS_SESSION_HOURS="$ore" "${inibisci[@]}" bash "$repo/phone/scripts/pc-build.sh" prototype || codice=$?

  # 7. Esito, in chiaro.
  python3 - "$stato" "$repo" "$inizio" "$codice" "$log" <<'PY'
import json, sys
from pathlib import Path
stato, repo, inizio, codice, log = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
sessioni = [p for p in (stato / "logs").glob("*/session.json") if p.stat().st_mtime >= inizio]
sessione = max(sessioni, key=lambda p: p.stat().st_mtime, default=None)
dettaglio = json.loads(sessione.read_text()) if sessione else {}
esito = dettaglio.get("state")
report = repo / "phone/uscita/gsi/prototipo/prototipo.json"
print()
if esito == "paused" and dettaglio.get("reason") == "interrupted":
    print("INTERROTTA: la sessione è stata fermata da fuori (Ctrl+C, spegnimento, riavvio")
    print("o uscita dall'utente), non da un errore. Il lavoro fatto è salvato.")
    print("Per continuare, rilancia lo stesso comando:")
    print("  bash phone/compila-telefono.sh")
    sys.exit(codice or 1)
if codice != 0:
    # Podman a volte perde il codice del contenitore (127): quello vero è nella sessione.
    print(f"ERRORE: la compilazione si è fermata (codice {dettaglio.get('exit_code', codice)}). Il lavoro già fatto resta sul disco.")
    print("Manda questi file a chi ti aiuta:")
    print(f"  {log}")
    if sessione:
        print(f"  {sessione.parent / 'build.log'}")
    sys.exit(codice)
if esito == "paused":
    print("SESSIONE FINITA: l'immagine NON è ancora pronta. Il lavoro fatto è salvato.")
    print("Puoi spegnere il PC. Per continuare, rilancia lo stesso comando:")
    print("  bash phone/compila-telefono.sh")
    sys.exit(0)
if esito == "completed" and report.is_file() and report.stat().st_mtime >= inizio:
    dati = json.loads(report.read_text())
    print("FATTO: immagini del prototipo pronte (chiavi di prova AOSP, non per l'uso quotidiano).")
    for immagine in dati.get("images", []):
        print(f"  {report.parent / immagine['name']}  {immagine['size'] / 1024**3:.2f} GiB  SHA-256 {immagine['sha256']}")
    print("NON installarle ancora sul telefono: prima vanno controllati partizioni, spazio e")
    print(f"firmware Motorola di ripristino. Manda {report} a chi ti aiuta.")
    sys.exit(0)
print(f"Esito non chiaro (stato della sessione: {esito}). Controlla il registro: {log}")
sys.exit(1)
PY
}

main "$@"; exit $?
