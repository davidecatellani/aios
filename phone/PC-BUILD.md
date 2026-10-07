# Compilare la GSI sul PC SoIA/Fedora

Questa procedura usa il PC Linux x86_64 con 32 GB di RAM e disco da 1 TB come
macchina di compilazione. Podman avvia un contenitore Ubuntu con gli strumenti
Android; sorgenti e compilazioni restano sul disco tra un'esecuzione e l'altra.
Non serve collegare il Motorola per preparare o compilare il sistema.

## Dal terminale del PC

Dal checkout di questo repository, come utente normale:

```bash
bash phone/scripts/pc-build.sh prototype
```

- `prototype` prepara e compila tutti i componenti in una sola esecuzione, poi
  estrae le immagini controllate in `phone/uscita/gsi/prototipo/`.
- `check` verifica architettura, disponibilità di Podman rootless, RAM, swap e
  spazio libero. Crea soltanto la cartella di lavoro e il file di lock;
  non scarica sorgenti né costruisce o avvia contenitori.
- `prepare` costruisce il contenitore, sincronizza AOSP `android-16.0.0_r3`
  e installa l'NDK r28c nella cartella di lavoro, dopo la verifica del checksum
  pubblicato da Google. Prepara anche Google Android 16 ARM64, il modello locale,
  il modello Whisper, F-Droid e KDE Connect, con revisioni/checksum fissati.
- `build` compila llama.cpp, Whisper ed eSpeak-ng e il prodotto `aios_gsi_arm64`,
  poi controlla SDK, Nova, motori, Google, app, modelli, permessi e immagini nei target-files.

Servono `podman`, `python3` e `flock` sul PC. Se ne manca uno, lo script si ferma
e lo segnala; non installa pacchetti nel sistema operativo. Su Fedora Silverblue
Podman è normalmente disponibile: va comunque verificato con `check`.

Il disco deve avere **almeno 300 GiB liberi prima del primo download**: 1 TB di
capacità non indica quanto spazio sia ancora disponibile. Su un checkout già
iniziato, il controllo richiede 30 GiB di margine; non è una stima dello spazio
ancora necessario per un download interrotto o per l'intera build. La prima
compilazione può richiedere molte ore e 32 GB possono richiedere swap. Lo script
mostra la swap esistente, senza crearla o modificare il PC.

Build e download usano quattro processi ciascuno. Per ridurre la pressione sulla
memoria si può usare `AIOS_BUILD_JOBS=2`. La cartella predefinita è
`~/soia-phone-build`; per un altro disco, impostare un percorso assoluto dedicato:

```bash
export AIOS_PC_BUILD_DIR=/percorso/disco/soia-phone-build
export AIOS_BUILD_JOBS=2
bash phone/scripts/pc-build.sh prepare
bash phone/scripts/pc-build.sh build
```

Serve accesso a Docker Hub, repository Ubuntu, GitHub, `android.googlesource.com`
`dl.google.com`, `registry.ollama.ai`, `huggingface.co` e `f-droid.org`. Le versioni di repo, AOSP, NDK e llama.cpp sono fissate negli
script. La base Ubuntu 24.04 e i suoi pacchetti ricevono gli aggiornamenti dei
repository: non si tratta di una build riproducibile bit per bit.

## Da GitHub Actions sullo stesso PC

È presente il workflow manuale `.github/workflows/telefono-gsi.yml`. Per usarlo,
i file devono prima essere pubblicati nel repository GitHub; la loro presenza
in questo checkout non li rende già disponibili online.

Sul PC, registrare il runner seguendo **Settings → Actions → Runners → New
self-hosted runner**, scegliendo Linux x64 e aggiungendo l'etichetta
`soia-android`. Il token di registrazione si inserisce sul PC, senza condividerlo
in chat. Avviare il runner con `./run.sh` e lasciare aperto quel terminale.

Quando il workflow è disponibile, in **Actions → SoIA GSI sul PC → Run workflow**
scegliere il ramo contenente queste modifiche e avviare `prototype`; le tre
fasi separate restano disponibili per diagnosi o ripresa. Il workflow ha
solo avvio manuale, permessi di lettura del repository e una sola esecuzione alla
volta; non parte automaticamente per pull request. Usare il runner per codice
fidato, perché esegue il checkout sul proprio PC.

I log di `prepare` e `build` sono conservati sotto
`~/soia-phone-build/logs/<esecuzione>-<tentativo>/`. Con il percorso predefinito,
GitHub li conserva anche come artefatti per sette giorni. Se si personalizza
`AIOS_PC_BUILD_DIR` sul runner, adeguare anche il percorso di upload nel workflow.

## Risultato e passo successivo

I target-files non firmati restano sotto
`~/soia-phone-build/sources/aosp-android-16.0.0_r3/out/target/product/`;
il percorso preciso appare nel log finale. Il binario nativo generato per Nova
resta nel checkout, in `phone/packages/apps/Nova/native/arm64-v8a/`, escluso da Git.
È installato come eseguibile in `/system_ext/bin/aios-llama-server`, richiesto dal
modulo Nova. Non è una libreria JNI rinominata: i file delle librerie di sistema
non hanno normalmente il permesso di esecuzione.

Il comando estrae `system.img`, `vbmeta.img` e `prototipo.json` sotto
`phone/uscita/gsi/prototipo/`: sono artefatti con chiavi di test AOSP, non release
firmate con le chiavi del progetto. Non pubblica immagini. La firma è un passo separato con
le chiavi del progetto, descritto in [GSI.md](GSI.md). Il contenitore monta soltanto
checkout e cartella di build; non monta USB, socket del motore dei contenitori o
cartelle esterne delle chiavi. Non esegue operazioni sul telefono.

Una build riuscita e il controllo dell'archivio non certificano il Motorola
XT2409-1: restano da verificare compatibilità vendor, avvio e funzioni hardware.
Lo stato dell'immagine e il piano di prova sono in [GSI.md](GSI.md) e
[EDGE50NEO.md](EDGE50NEO.md).

## Verifiche eseguite in cloud

Il contenitore è stato costruito e avviato tramite Docker; per il proxy cloud
sono stati forniti durante la prova il suo indirizzo e il bundle CA fidato del
cloud, senza disabilitare TLS. I tre motori sono stati realmente compilati per ARM64
con NDK r28c: usano il linker Android e segmenti ELF allineati a 16 KiB. Sono stati
compilati anche i sorgenti Kotlin e le risorse di Nova; il controllo delle risorse
usa il framework Android 15 e non produce un'app installabile.

I test degli script simulano i comandi Podman e gli archivi AOSP. La compatibilità
con Podman rootless e SELinux sul PC Fedora, il workflow GitHub e la build Android
completa non sono ancora stati provati sulla macchina reale.
