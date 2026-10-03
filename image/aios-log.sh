#!/bin/bash
# Raccoglie i registri dell'installazione di AIOS su una chiavetta (non quella di installazione).
#
# Nella chiavetta d'installazione è già presente come comando: Ctrl+Alt+F2, poi scrivi  aios-log
# Con una chiavetta vecchia: copia questo file sulla chiavetta dei registri come log.sh, rinominala LOG
# (in Windows: tasto destro › Rinomina) e nel terminale dell'installatore scrivi:
#     mkdir -p /run/u; mount -L LOG /run/u; bash /run/u/log.sh
set -u
DEST=/run/aios-log

# Chiavette candidate: partizioni FAT/exFAT/NTFS che non stanno sul disco d'installazione (iso9660)
# né su un disco su cui l'installatore ha già montato qualcosa.
candidates() {
    lsblk -nPp -o NAME,FSTYPE,LABEL,MOUNTPOINT,PKNAME | while read -r line; do
        NAME="" FSTYPE="" LABEL="" MOUNTPOINT="" PKNAME=""
        eval "$line"  # NAME="…" FSTYPE="…": lsblk -P protegge i caratteri speciali
        case "$FSTYPE" in vfat|exfat|ntfs) ;; *) continue ;; esac
        [ -n "$PKNAME" ] || continue
        lsblk -rno FSTYPE "$PKNAME" | grep -q iso9660 && continue
        lsblk -rno MOUNTPOINT "$PKNAME" | grep -q "^/mnt/sys" && continue
        echo "$NAME|${MOUNTPOINT}|${LABEL}"
    done
}

found=$(candidates)
mounted_here=""
if echo "$found" | grep -q "|/run/u|"; then  # già montata dal comando con log.sh
    target=/run/u
else
    pick=$(echo "$found" | awk -F'|' '$3=="LOG"{print $1; exit}')
    [ -n "$pick" ] || pick=$(echo "$found" | awk -F'|' 'NF && $2==""{print $1; exit}')
    if [ -z "$pick" ]; then
        echo "Non trovo una chiavetta per i registri: inseriscine una (FAT, exFAT o NTFS) e riprova."
        echo "Dischi visti:"; lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
        exit 1
    fi
    mkdir -p "$DEST"
    if ! mount "$pick" "$DEST"; then
        echo "Non riesco ad aprire $pick."; exit 1
    fi
    target=$DEST
    mounted_here=1
fi

out="$target/log-aios-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out"
cp /tmp/*.log "$out"/ 2>/dev/null
cp /tmp/anaconda-tb-* "$out"/ 2>/dev/null
cp /tmp/ks*.cfg /run/install/ks.cfg "$out"/ 2>/dev/null
journalctl -b --no-pager > "$out/journal.txt" 2>/dev/null
lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,PARTTYPE,MOUNTPOINT > "$out/dischi.txt" 2>/dev/null
dmesg > "$out/dmesg.txt" 2>/dev/null
cd /
sync
if [ -n "$mounted_here" ] || [ "$target" = /run/u ]; then
    umount "$target" 2>/dev/null || { sleep 2; sync; umount -l "$target"; }
fi
echo
echo "Fatto: registri salvati nella cartella $(basename "$out") della chiavetta."
echo "Puoi toglierla. Ctrl+Alt+F6 (o F1) per tornare all'installatore."
