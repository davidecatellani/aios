#!/usr/bin/env bash
# SoIA sul tuo Linux: Nova, i servizi in sottofondo, icone e scorciatoia.
#
#   ./install.sh                 installa (chiede conferma prima di usare sudo o la rete)
#   ./install.sh --si            installa senza domande
#   ./install.sh --disinstalla   toglie SoIA (i tuoi dati restano; --cancella-dati per toglierli)
#
# Altre opzioni: --senza-pacchetti (non usa il gestore dei pacchetti), --senza-ollama,
# --modello NOME (modello AI iniziale, predefinito qwen2.5:1.5b-instruct).
# Tutto va nella tua cartella personale: ~/.local/share/aios (programma e dati),
# ~/.config/systemd/user (servizi), ~/.local/bin (comandi).
set -euo pipefail

QUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATI="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
VENV="$DATI/aios/venv"
BIN="$HOME/.local/bin"
UNITA="$CONFIG/systemd/user"
SERVIZI=(aios-learn aios-agenda aios-mail aios-telefono)
MODELLO="qwen2.5:1.5b-instruct"
SI=0; PACCHETTI=1; OLLAMA=1; AZIONE=installa; CANCELLA_DATI=0

for arg in "$@"; do
  case "$arg" in
    --si|-y) SI=1 ;;
    --senza-pacchetti) PACCHETTI=0 ;;
    --senza-ollama) OLLAMA=0 ;;
    --disinstalla) AZIONE=disinstalla ;;
    --cancella-dati) CANCELLA_DATI=1 ;;
    --modello=*) MODELLO="${arg#*=}" ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "Opzione sconosciuta: $arg (vedi --help)" >&2; exit 1 ;;
  esac
done

passo() { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
nota() { printf '  %s\n' "$*"; }
chiedi() {  # chiedi "domanda" → 0 se sì
  [ "$SI" = 1 ] && return 0
  read -r -p "  $1 [S/n] " r || r=n
  [[ -z "$r" || "$r" =~ ^[sSyY] ]]
}
ha() { command -v "$1" >/dev/null 2>&1; }
systemd_utente() { ha systemctl && systemctl --user show-environment >/dev/null 2>&1; }

# --- disinstallare ------------------------------------------------------------------------------
if [ "$AZIONE" = disinstalla ]; then
  passo "Tolgo SoIA"
  if systemd_utente; then
    for s in "${SERVIZI[@]}"; do systemctl --user disable --now "$s.service" 2>/dev/null || true; done
  fi
  for s in "${SERVIZI[@]}"; do rm -f "$UNITA/$s.service"; done
  for f in "$VENV"/bin/aios-*; do [ -e "$f" ] && rm -f "$BIN/$(basename "$f")"; done
  rm -f "$DATI/applications/org.aios."*.desktop "$CONFIG/autostart/org.aios.Welcome-autostart.desktop"
  find "$DATI/icons/hicolor" -name 'org.aios.*' -delete 2>/dev/null || true
  rm -rf "$VENV"
  if ha gsettings && gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings >/dev/null 2>&1; then
    lista="$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)"
    nuova="$(python3 -c 'import ast,sys; l=ast.literal_eval(sys.argv[1].replace("@as ","")); print([p for p in l if "aios-nova" not in p])' "$lista")"
    gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$nuova" || true
  fi
  if [ "$CANCELLA_DATI" = 1 ]; then
    rm -rf "$DATI/aios" "$CONFIG/aios"
    nota "Cancellati anche i dati di SoIA (indice, agenda, posta, impostazioni)."
  else
    nota "I tuoi dati restano in $DATI/aios e $CONFIG/aios (per toglierli: --disinstalla --cancella-dati)."
  fi
  nota "Fatto. Ollama e i modelli AI non li tocco: «ollama rm NOME» per liberare spazio."
  exit 0
fi

# --- installare ---------------------------------------------------------------------------------
[ "$(uname -s)" = Linux ] || { echo "SoIA si installa su Linux." >&2; exit 1; }
printf '\033[1mAIOS\033[0m — installo Nova, l'"'"'assistente AI locale, e i servizi di SoIA.\n'

if [ "$PACCHETTI" = 1 ]; then
  passo "Programmi di sistema"
  if ha dnf; then
    GESTORE=(sudo dnf install -y)
    PKG=(python3 python3-pip python3-setuptools python3-gobject gtk4 webkitgtk6.0 ffmpeg-free poppler-utils
         libnotify wl-clipboard kde-connect android-tools ydotool bluez flatpak git openssl libsecret)
  elif ha apt-get; then
    GESTORE=(sudo apt-get install -y)
    PKG=(python3 python3-venv python3-pip python3-setuptools python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0 ffmpeg
         poppler-utils libnotify-bin wl-clipboard kdeconnect adb fastboot ydotool bluez flatpak git openssl libsecret-tools)
  elif ha pacman; then
    GESTORE=(sudo pacman -S --needed --noconfirm)
    PKG=(python python-pip python-setuptools python-gobject gtk4 webkitgtk-6.0 ffmpeg poppler libnotify wl-clipboard
         kdeconnect android-tools ydotool bluez-utils flatpak git openssl libsecret)
  else
    GESTORE=()
    nota "Gestore dei pacchetti non riconosciuto: installa a mano Python 3, PyGObject, GTK 4, WebKitGTK 6, ffmpeg, poppler."
  fi
  if [ ${#GESTORE[@]} -gt 0 ]; then
    nota "Servono: ${PKG[*]}"
    if chiedi "Li installo con ${GESTORE[0]} ${GESTORE[1]}? (chiede la password)"; then
      [ "${GESTORE[1]}" = apt-get ] && sudo apt-get update
      if ! "${GESTORE[@]}" "${PKG[@]}"; then
        # un nome non disponibile fa fallire tutto: si riprova uno per uno
        MANCANTI=()
        for p in "${PKG[@]}"; do "${GESTORE[@]}" "$p" >/dev/null 2>&1 || MANCANTI+=("$p"); done
        [ ${#MANCANTI[@]} -eq 0 ] || nota "Non disponibili qui: ${MANCANTI[*]} (SoIA funziona lo stesso, con meno funzioni)."
      fi
    fi
  fi
fi

passo "Nova e i programmi di SoIA"
ha python3 || { echo "Serve Python 3." >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Serve Python 3.10 o più recente." >&2; exit 1; }
mkdir -p "$DATI/aios" "$BIN"
# --system-site-packages: così si usano GTK e WebKit installati dal sistema (PyGObject)
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install --quiet --disable-pip-version-check --no-build-isolation "$QUI/copilot" 2>/dev/null \
  || "$VENV/bin/pip" install --quiet --disable-pip-version-check "$QUI/copilot"
for f in "$VENV"/bin/aios-*; do ln -sf "$f" "$BIN/$(basename "$f")"; done
nota "Comandi in $BIN ($(ls "$VENV"/bin/aios-* | wc -l) programmi)."
case ":$PATH:" in *":$BIN:"*) ;; *) nota "Aggiungi $BIN al PATH (di solito basta uscire e rientrare)." ;; esac

passo "Icone, menu e benvenuto"
mkdir -p "$DATI/applications" "$CONFIG/autostart" "$DATI/icons"
cp -r "$QUI/copilot/data/icons/hicolor" "$DATI/icons/"
for f in "$QUI"/copilot/data/org.aios.Copilot.desktop "$QUI"/copilot/data/org.aios.Welcome.desktop; do
  sed "s#^Exec=aios-#Exec=$BIN/aios-#" "$f" > "$DATI/applications/$(basename "$f")"
done
# il benvenuto parte al prossimo accesso, una volta sola
sed -e "s#^Exec=aios-#Exec=$BIN/aios-#" -e '/^# /d' "$QUI/copilot/data/org.aios.Welcome-autostart.desktop" \
  > "$CONFIG/autostart/org.aios.Welcome-autostart.desktop"
ha gtk-update-icon-cache && gtk-update-icon-cache -q -t "$DATI/icons/hicolor" 2>/dev/null || true
ha update-desktop-database && update-desktop-database -q "$DATI/applications" 2>/dev/null || true

passo "Scorciatoia per chiamare Nova"
if ha gsettings && gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings >/dev/null 2>&1; then
  TASTO='<Super>space'
  sorgenti="$(gsettings get org.gnome.desktop.input-sources sources 2>/dev/null || echo '[]')"
  if [ "$(python3 -c 'import ast,sys; print(len(ast.literal_eval(sys.argv[1].replace("@a(ss) ",""))))' "$sorgenti")" -gt 1 ]; then
    TASTO='<Super>n'  # con più lingue di tastiera, Super+Spazio cambia lingua: non lo tocco
  else
    gsettings set org.gnome.desktop.wm.keybindings switch-input-source "['XF86Keyboard']" 2>/dev/null || true
  fi
  PERCORSO=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/aios-nova/
  lista="$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)"
  nuova="$(python3 -c 'import ast,sys; l=ast.literal_eval(sys.argv[1].replace("@as ","")); p=sys.argv[2]; print(l if p in l else l+[p])' "$lista" "$PERCORSO")"
  gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$nuova"
  S="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$PERCORSO"
  gsettings set "$S" name "Nova"
  gsettings set "$S" command "$BIN/aios-copilot"
  gsettings set "$S" binding "$TASTO"
  nota "GNOME: ${TASTO/<Super>/Super+} apre Nova."
elif ha kwriteconfig6 || ha kwriteconfig5; then
  echo "X-KDE-Shortcuts=Meta+Space" >> "$DATI/applications/org.aios.Copilot.desktop"
  nota "KDE: Meta+Spazio apre Nova (se non funziona: Impostazioni › Scorciatoie › Nova)."
else
  nota "Imposta una scorciatoia da tastiera che lanci: $BIN/aios-copilot"
fi

if [ "$OLLAMA" = 1 ]; then
  passo "Modello AI locale (Ollama)"
  if ! ha ollama; then
    nota "Ollama esegue i modelli AI sul tuo computer, senza mandare nulla in rete."
    if chiedi "Installo Ollama con lo script ufficiale (https://ollama.com/install.sh, chiede la password)?"; then
      curl -fsSL https://ollama.com/install.sh | sh
    else
      nota "Senza Ollama, Nova capisce già i comandi comuni; per le domande libere: https://ollama.com"
    fi
  fi
  if ha ollama; then
    if chiedi "Scarico il modello iniziale $MODELLO (circa 1 GB)?"; then
      ollama pull "$MODELLO" || nota "Scaricamento non riuscito: riprova con «ollama pull $MODELLO»."
    fi
    nota "Poi Nova propone in automatico modelli migliori adatti al tuo computer («che modelli posso usare?»)."
  fi
fi

passo "Servizi in sottofondo"
mkdir -p "$UNITA"
for s in "${SERVIZI[@]}"; do
  # percorso completo: i servizi dell'utente non vedono ~/.local/bin nel PATH
  sed "s#^ExecStart=aios-#ExecStart=$BIN/aios-#" "$QUI/copilot/data/$s.service" > "$UNITA/$s.service"
done
if systemd_utente; then
  systemctl --user daemon-reload
  systemctl --user enable --now "${SERVIZI[@]/%/.service}" >/dev/null 2>&1 \
    && nota "Attivi: apprendimento a riposo, agenda, posta, telefono." \
    || nota "Non sono riuscito ad avviare tutti i servizi: «systemctl --user status aios-learn» per i dettagli."
else
  nota "systemd per l'utente non è disponibile qui: servizi copiati in $UNITA, da attivare al prossimo accesso."
fi

passo "Fatto"
cat <<FINE
  Apri Nova dal menu delle applicazioni, con la scorciatoia, oppure:
    $BIN/aios-welcome      il benvenuto (si apre anche al prossimo accesso)
    $BIN/aios-copilot      Nova
  Per collegare il telefono: installa l'app KDE Connect sul telefono e di' «Nova, collega il telefono».
  Per aggiornare SoIA: git pull && ./install.sh   ·   Per toglierlo: ./install.sh --disinstalla
FINE
