# Primo dispositivo: Motorola Edge 50 Neo

Obiettivo: un'immagine completa SoIA, con Nova come assistente di sistema, non
soltanto un'app installata sopra Android. La compatibilità hardware non è ancora
verificata; non è stato aggiunto un bersaglio dedicato a `dispositivi.json`.

La strada scelta è la [GSI generica Android 16 ARM64](GSI.md), condivisa con altri
telefoni Treble, mantenendo kernel e firmware vendor Motorola. Treble e arm64 da
soli non dimostrano il funzionamento di modem, fotocamera, sensori o impronta.

Dati riferiti dall'utente: **XT2409-1**, **Android 16**, **Sblocco OEM attivato**.
Il **7 ottobre 2026** l'utente ha confermato di aver sbloccato il bootloader,
dopo aver ottenuto l'identificativo tramite Fastboot su Windows e ricevuto la
chiave dal portale Motorola. Lo stato è riferito dall'utente; resta da rilevare
il report delle proprietà del telefono. Seriale, identificativo e chiave di
sblocco non sono conservati nel repository. Non è ancora stata installata SoIA.

## Dati necessari prima di scegliere la base

Restano da rilevare la variante operatore/regionale e le proprietà vendor,
confermando nel report anche lo stato del bootloader dopo lo sblocco.
Lo sblocco non certifica la compatibilità hardware con la GSI.
Non bloccare di nuovo il bootloader con una GSI sperimentale.

Con il telefono collegato a un computer con ADB e Debug USB già autorizzato:

```bash
python3 phone/scripts/verifica-telefono.py
```

Questo comando legge solo un elenco esplicito di proprietà. Non sblocca, riavvia,
installa o modifica il telefono; il report non include il seriale. Con più telefoni,
selezionare quello corretto con `--serial`. Il risultato non certifica la
compatibilità con SoIA e non avvia l'installatore.

Prima di una prima prova dell'immagine:

- scegliere un ramo/tag AOSP fissato in base al firmware vendor rilevato;
  non usare automaticamente il ramo mobile `android-latest-release`;
- accertare la struttura delle partizioni, fastbootd e lo spazio disponibile;
- predisporre backup verificato e un percorso di ritorno al firmware Motorola
  esatto per la variante, con artefatti e istruzioni attendibili;
- verificare su hardware avvio, chiamate/dati/SMS, fotocamera, impronta, NFC,
  Wi-Fi/Bluetooth, sospensione e consumo a riposo.

Non ci sono ancora risultati di questi controlli né un'immagine pronta da flashare.
L'ambiente cloud corrente non ha spazio sufficiente per il checkout e la build
AOSP/LineageOS da almeno 300 GB descritti in `README.md`. La procedura per
compilare sul PC SoIA/Fedora è in [PC-BUILD.md](PC-BUILD.md).

## Primo sviluppo comune al telefono

Nova ora gestisce le conferme inviate dal PC: mostra azione e avviso di privacy,
invia la decisione e annulla se il dialogo viene chiuso. Ogni conferma del PC ha
un identificativo, così una risposta precedente non autorizza l'azione seguente.
I client precedenti senza identificativo restano compatibili; aggiornare anche il
PC per la protezione completa dalle risposte obsolete.

Se una richiesta già avviata sul PC perde il collegamento o scade, Nova segnala che
va controllato il suo stato sul PC, senza eseguirla nuovamente sul modello locale.
Il protocollo è verificato sul JVM e il collegamento PC con test HTTPS, ma l'interfaccia
e il ciclo di vita dell'app restano da provare sul telefono.

Il [prototipo integrato](PROTOTIPO.md) ora comprende AI e voce offline, comandi,
identità e dati cifrati, collegamento al PC, Google, F-Droid e KDE Connect. Si
prepara e compila sul PC con `bash phone/scripts/pc-build.sh prototype`. Non
aggiunge risultati hardware: la build completa e la prova sul Motorola restano
da eseguire.
