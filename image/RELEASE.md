**Anteprima** di AIOS per PC: Fedora Silverblue con Nova e i servizi di AIOS già dentro.
Non è ancora stata provata su molti PC: **provala prima in una macchina virtuale** (GNOME Boxes,
VirtualBox) o su un PC senza dati importanti.

### Preparare la chiavetta (8 GB o più)

1. Scarica i file `aios-installazione.iso…` qui sotto.
2. Se la ISO è divisa in pezzi (`.parte0`, `.parte1`, …), riuniscila:
   - **Windows** (PowerShell o Prompt dei comandi, nella cartella Download):
     `cmd /c copy /b aios-installazione.iso.parte0 + aios-installazione.iso.parte1 aios-installazione.iso`
     (aggiungi `+ aios-installazione.iso.parte2` se c'è)
   - **Linux / macOS**: `cat aios-installazione.iso.parte* > aios-installazione.iso`
3. Controlla che sia integra (facoltativo): il codice di `sha256sum aios-installazione.iso`
   (Windows: `certutil -hashfile aios-installazione.iso SHA256`) deve coincidere con il file `.sha256`.
4. Scrivila sulla chiavetta con [Fedora Media Writer](https://fedoraproject.org/workstation/download)
   («Seleziona un file .iso») o [balenaEtcher](https://etcher.balena.io). La chiavetta viene cancellata.

### Installare

1. Avvia il PC dalla chiavetta (all'accensione: F12, F11, F8 o Esc per il menu di avvio;
   se non parte, disattiva «Secure Boot» nel BIOS).
2. L'installatore è già in italiano: scegli **Destinazione dell'installazione**, cioè il disco.
   ⚠️ Il disco scelto può essere cancellato: se sul PC c'è Windows o dei dati, scegli con cura
   o fai prima un backup.
   Se il disco aveva già Linux (per esempio Ubuntu), in «Recupera spazio» premi **«Elimina tutto»**,
   compresa la partizione EFI: se ne resta una vecchia, l'installazione si ferma con l'errore
   «Bootloader write config: grub2-mkconfig».
3. Al riavvio entra con utente **aios** e password **aios**, poi cambiala subito
   (Impostazioni › Utenti). Premi **Super+Spazio** per parlare con Nova.
