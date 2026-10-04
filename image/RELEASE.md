**Anteprima** di AIOS per PC: Fedora Silverblue con Nova e i servizi di AIOS già dentro.
Non è ancora stata provata su molti PC: **provala prima in una macchina virtuale** (GNOME Boxes,
VirtualBox) o su un PC senza dati importanti.

### Aggiornare un PC con AIOS già installato (niente formattazione)

- **Da GitHub**: di' a Nova «collega GitHub per gli aggiornamenti» (una volta sola, con un tuo
  permesso di sola lettura); poi le nuove versioni arrivano da sole e si applicano al riavvio.
- **Da chiavetta**: copia su una chiavetta tutti i file `aios-aggiornamento…` (senza riunirli) e
  inseriscila nel PC: Nova propone di preparare l'aggiornamento («aggiorna dalla chiavetta»).

Dati, impostazioni e app restano; la versione precedente resta disponibile all'avvio.

### Preparare la chiavetta d'installazione (8 GB o più, solo per la prima installazione)

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

0. **Togli schede SD e altre chiavette o dischi USB** (l'installatore comunque non li propone più).
1. Avvia il PC dalla chiavetta (all'accensione: F12, F11, F8 o Esc per il menu di avvio;
   se non parte, disattiva «Secure Boot» nel BIOS).
2. L'installatore è già in italiano: scegli **Destinazione dell'installazione**, cioè il disco.
   ⚠️ Il disco scelto può essere cancellato: se sul PC c'è Windows o dei dati, scegli con cura
   o fai prima un backup.
   Se il disco aveva già Linux (per esempio Ubuntu), in «Recupera spazio» premi **«Elimina tutto»**,
   compresa la partizione EFI: se ne resta una vecchia, l'installazione si ferma con l'errore
   «Bootloader write config: grub2-mkconfig».
3. Al riavvio compare la schermata d'accesso di AIOS: password **aios**. Poi cambiala subito
   (Impostazioni › Password). Al primo accesso Nova ti accoglie: nome, voce, Wi-Fi e cosa collegare.
   Premi **Super+Spazio** (o di' «Nova») per parlarle; il tasto **Super** da solo torna alla schermata.

### Kernel 6.18 LTS

Le anteprime con «kernel LTS 6.18» nel titolo usano un kernel non firmato da Fedora: **disattiva il
Secure Boot** nel BIOS prima di avviare, altrimenti il PC non parte.

### Se qualcosa va storto

Non spegnere: inserisci un'altra chiavetta, premi **Ctrl+Alt+F2**, scrivi **`aios-log`** e premi
Invio. I registri finiscono in una cartella `log-aios-…` sulla chiavetta (Ctrl+Alt+F6 per tornare
all'installatore). Caricali su GitHub in un nuovo branch e avvisa.
