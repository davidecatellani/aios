# SoIA generico: GSI Android 16 ARM64

La versione comune a più marche è una **Generic System Image (GSI)** AOSP con Nova.
Sostituisce il sistema Android, riutilizzando il kernel e le implementazioni hardware
del produttore presenti sul telefono. Non è un firmware universale: non sostituisce
bootloader, modem o driver e non garantisce da sola il funzionamento delle periferiche.

## Prima variante

- bersaglio: `gsi`, prodotto `aios_gsi_arm64`, variante `user`;
- base fissata: `android-16.0.0_r3`, release configuration `trunk_staging`;
- architettura: ARM64, telefoni Treble; primo gruppo di candidati con Android 16;
- artefatti: `system.img` e `vbmeta.img`, catalogo firmato con SHA-256;
- stato: configurazione e controlli verificati, immagine non ancora compilata.

Il tag è stato verificato nel manifest e nel mirror ufficiale AOSP `platform_build`.
La configurazione AOSP GSI `BoardConfigGsiCommon.mk` colloca `system_ext` e `product`
sotto `system`, quindi Nova e i suoi permessi possono viaggiare in una sola immagine.
`gsi_release.mk` è incluso esplicitamente anche per il nome prodotto SoIA.

Il catalogo di questa prima variante contiene `min_sdk=36` e `max_sdk=36`, così
l'installatore non la sceglie automaticamente per telefoni Android 15 o 17.
È una restrizione prudente del primo gruppo di prove, non la dimostrazione che
Android 16 GSI sia incompatibile con tutti i firmware precedenti. Ampliare
l'intervallo richiede verifiche VINTF/vendor e prove hardware. Il tag fissato va
aggiornato alle patch di sicurezza prima di una distribuzione pubblica.

## Controllo di un telefono

```bash
python3 phone/scripts/verifica-telefono.py
```

Legge soltanto proprietà selezionate via ADB. Distingue requisiti GSI, permesso
di sblocco OEM e bootloader effettivamente sbloccato; nessuno di questi controlli
certifica la compatibilità reale. Non avvia un flash e non riavvia il telefono.

Per gran parte dei dispositivi retail occorre il bootloader sbloccato per installare
una GSI. La possibilità di sbloccarlo dipende dalla variante: l'interruttore OEM
da solo non è una conferma di idoneità del produttore. Per prove tramite DSU, quando
supportate dalla variante, vanno verificati separatamente requisiti e firma dell'immagine.

## Build su una macchina adeguata

Per il PC SoIA/Fedora, seguire [la procedura con Podman](PC-BUILD.md): include il
contenitore degli strumenti, l'installazione verificata dell'NDK e il workflow
manuale per un runner locale. I comandi seguenti descrivono la build diretta.

Prerequisiti: Linux x86_64, almeno 300 GiB liberi, 32–64 GB di RAM,
strumenti di build AOSP, `repo`, NDK e sorgenti di llama.cpp. Il checkout richiede
accesso a `android.googlesource.com` e ai servizi di download AOSP. La lista concreta
va verificata sulla macchina di build; non disabilitare controlli TLS o firme.

```bash
export AIOS_SORGENTI=/percorso/disco/aios-android
phone/scripts/prepara.sh gsi
phone/scripts/google-android.sh
python3 phone/scripts/prepara-prototipo.py --cache ~/.cache/aios-prototype
phone/scripts/llama-android.sh
phone/scripts/voce-android.sh
phone/scripts/compila.sh gsi
# Le chiavi vanno create e conservate fuori dal repository:
phone/scripts/chiavi.sh "$AIOS_SORGENTI/aosp-android-16.0.0_r3"
phone/scripts/firma.sh gsi
```

`prepara.sh` controlla lo spazio prima di scaricare o creare il checkout e limita
la sincronizzazione a quattro job (`AIOS_SYNC_JOBS` per una macchina diversa).
`firma.sh` cerca solo target-files del prodotto selezionato. Si può indicare un
archivio preciso con `AIOS_TARGET_FILES`, senza scegliere automaticamente un altro
prodotto compilato nello stesso checkout.

Prima di estrarre gli artefatti firmati, `verifica-gsi.py` controlla che siano presenti
le immagini, Nova, i tre motori nativi, Google, KDE Connect, F-Droid e i modelli
offline con le loro impronte, il file XML dei permessi privilegiati e
il livello SDK corretto. Lo stesso controllo è eseguibile direttamente:

```bash
python3 phone/scripts/verifica-gsi.py /percorso/target-files.zip --sdk 36
```

È un controllo di contenuto; non sostituisce la verifica crittografica, la build
Soong completa, i test VINTF o la prova di avvio. I test automatici del controllo
usano archivi sintetici, non un'immagine Android realmente compilata.

## Prima distribuzione sperimentale

Prima di proporre comandi di installazione servono artefatti firmati e verificati,
backup recuperabile e firmware stock esatto per tornare indietro. Non richiedere
di ribloccare il bootloader con una GSI sperimentale. Le istruzioni di AVB/vbmeta
e fastbootd devono essere verificate per la variante del telefono.

La matrice di prove per ogni famiglia deve includere: avvio, chiamate/dati/SMS,
fotocamera, impronta, NFC, audio, Wi-Fi/Bluetooth, ricarica, sospensione e consumo
a riposo. Il primo dispositivo è il Motorola Edge 50 Neo XT2409-1 con Android 16;
vedi [stato e dati disponibili](EDGE50NEO.md).

L'ambiente cloud corrente ha meno di 30 GiB liberi e blocca l'accesso a
`android.googlesource.com`. Non permette di eseguire ora la build completa.

La variante integrata, i componenti Google e il comando unico sono descritti in
[PROTOTIPO.md](PROTOTIPO.md). La build esporta anche immagini con chiavi di test
AOSP in `uscita/gsi/prototipo/`; la firma di release resta un passaggio separato.
