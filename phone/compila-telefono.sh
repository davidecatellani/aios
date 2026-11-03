#!/usr/bin/env bash
# SoIA per telefono, tutto con un comando sul PC Fedora/SoIA (dalla cartella del repository):
# aggiorna il codice, controlla lo spazio, legge il telefono se è collegato, compila a
# sessioni riprendibili e alla fine dice com'è andata. Non scrive mai sul telefono.
#
#   bash phone/compila-telefono.sh                    sessione di 8 ore, poi rilancialo
#   bash phone/compila-telefono.sh --ore 12           sessione più lunga (0: fino alla fine)
#   bash phone/compila-telefono.sh --senza-aggiornare non scaricare le ultime correzioni
#   bash phone/compila-telefono.sh --ignora-spazio    solo se sai che lo spazio basta
set -euo pipefail

SPAZIO_TOTALE_GIB=300   # sorgenti AOSP + compilazione (PC-BUILD.md)

uso() { sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

main() {
  local repo ore="${AIOS_SESSION_HOURS:-8}" aggiorna=1 controlla_spazio=1 argomenti=("$@")
  repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  while [ $# -gt 0 ]; do
    case "$1" in
      --ore) [ $# -ge 2 ] || { echo "--ore vuole un numero di ore." >&2; return 2; }; ore="$2"; shift 2 ;;
      --senza-aggiornare) aggiorna=0; shift ;;
      --ignora-spazio) controlla_spazio=0; shift ;;
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
esito = json.loads(sessione.read_text()).get("state") if sessione else None
report = repo / "phone/uscita/gsi/prototipo/prototipo.json"
print()
if codice != 0:
    print(f"ERRORE: la compilazione si è fermata (codice {codice}). Il lavoro già fatto resta sul disco.")
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
