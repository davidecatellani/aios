# Prototipo SoIA per smartphone

Questa variante integra le funzioni comuni in **una GSI Android 16 ARM64**, con
Play Store e servizi Google come richiesto. Il primo telefono è il Motorola Edge
50 Neo XT2409-1; il bootloader è stato dichiarato sbloccato dall'utente.

Il codice e i componenti nativi sono presenti. **L'immagine Android completa non
è ancora stata compilata né avviata sul telefono.** La presenza di Treble non
garantisce chiamate, fotocamera, NFC, impronta o compatibilità vendor.

## Un comando sul PC

Sul PC SoIA/Fedora Linux x86_64, dal checkout del repository:

```bash
bash phone/scripts/pc-build.sh prototype
```

Servono Podman rootless, Python 3, flock, almeno 300 GiB liberi e accesso ai servizi
di download. I 32 GB di RAM sono utilizzabili con quattro job; lo script mostra
la swap disponibile. Download, sorgenti e compilazioni sono conservati per
riprendere una build interrotta. La prima compilazione richiede diverse ore.

Il comando prepara AOSP, NDK, Google, modelli e app, compila i tre motori nativi,
compila Android, controlla il contenuto dei target-files ed estrae:

```text
phone/uscita/gsi/prototipo/system.img
phone/uscita/gsi/prototipo/vbmeta.img
phone/uscita/gsi/prototipo/prototipo.json
```

Il report contiene dimensioni, SHA-256 e componenti offline. Questi artefatti
usano le **chiavi di test AOSP**; la firma con chiavi proprie rimane disponibile
per una release tramite `chiavi.sh` e `firma.sh`. Nessun comando della build
sblocca, riavvia, cancella dati o scrive sul telefono. Il flash richiede prima
il controllo delle partizioni e dello spazio reale, oltre al firmware Motorola
esatto per il ripristino. Il modello e Google aumentano la dimensione di system;
la capienza virtuale di 8 GiB della build non aumenta quella del telefono.

## Funzioni integrate

| Funzione | Implementazione e condizioni |
|---|---|
| Conversazione AI offline | Qwen2.5 1.5B Instruct GGUF già nell'immagine, llama.cpp su CPU; avvio su richiesta e arresto dopo 60 secondi di inattività |
| Ragionamento con il PC | Modello del PC tramite `/api/modello`, strumenti eseguiti sul telefono; fallback locale se il PC è assente |
| «Chiedi al PC …» | Agent del PC, conferme esplicite sul telefono, allegati scaricabili; nessuna ripetizione automatica dopo un invio incerto |
| Dettatura offline | Whisper tiny multilingue, microfono e trascrizione italiani; audio temporaneo cancellato, nessun invio audio in rete |
| Voce italiana | eSpeak-ng offline; lettura delle risposte attivabile nelle impostazioni |
| «Ehi Nova» | Ascolto software facoltativo, servizio microfono visibile e arrestabile, stop sotto il 15%; nessun avvio automatico dopo reboot |
| Assistente di sistema | Ruolo Android scelto dall'utente; apertura di Nova tramite assistente, senza forzare l'assegnazione |
| Comandi rapidi | «Apri …», «chiama …», «nota …», «mostra note/agenda/profilo/app», torcia e impostazioni |
| Strumenti del telefono | App, dialer, composizione SMS, ricerca web, volume, torcia, timer; modifiche confermate, invio SMS finale manuale |
| Dati personali | Note, eventi, promemoria e profilo offline; promemoria Android non esatti che rispettano Doze |
| Identità SoIA | Certificato del PC, richieste Ed25519, revoche e rotazioni X25519; chiavi cifrate con Android Keystore |
| Sincronizzazione | Agenda, note e profilo via operazioni cifrate ChaCha20-Poly1305, conflitti LWW/HLC e tombstone; richiede identità SoIA configurata sul PC |
| File e foto | Elenco/ricerca file condivisi dal PC, salvataggio con selettore Android, invio esplicito foto/video al PC |
| Telecomando | Touchpad a trascinamento/rilascio, clic, scorrimento e tastiera del PC; richiede backend input sul PC |
| Continuità telefono↔PC | KDE Connect incluso per notifiche, SMS, appunti, file, foto e controllo remoto secondo i plugin disponibili; abbinamento e permessi nell'app |
| App e Google | Play Store, Play Services, Google Services Framework e setup tramite MindTheGapps Android 16 ARM64; F-Droid incluso |
| Backup e modelli | Esportazione/importazione di agenda, note e profilo; import GGUF e verifica dei modelli; download mancanti riparabili |
| Energia | Sincronizzazione pianificata con batteria non bassa; download mancanti in carica, su rete non a consumo e a telefono inattivo |

In Nova, **«Prototipo: app, voce, dati e PC»** raccoglie tutte le funzioni. Si
concedono i permessi Android necessari e si sceglie Nova come assistente. Per
SoIA si usa il QR del PC; KDE Connect mantiene il proprio abbinamento. Google
richiede il normale setup dell'account. Il bootloader sbloccato e una GSI non
certificata possono impedire Play Integrity, pagamenti e alcune app bancarie.

Il backup esportato contiene i dati in chiaro e lo segnala prima di salvarlo.
Non esporta le chiavi del Keystore, i token di abbinamento o la conversazione.
I dati cifrati già sul telefono vengono conservati quando si entra nell'identità
del PC o cambia la chiave, anche se l'app si riavvia prima della ricifratura.

## Contenuto verificato e limiti

Le revisioni AOSP, NDK, llama.cpp, Whisper, eSpeak-ng e MindTheGapps sono fissate.
La prima preparazione risolve modello e APK da metadati HTTPS, registra revisioni,
dimensioni e SHA-256 in un lock persistente e controlla i blob prima di includerli.
Un errore di download conserva il file precedente. Il controllo GSI rifiuta
modelli mancanti/alterati, APK o componenti Google mancanti e SDK errato.

I sorgenti e binari Google restano nella cache della build: nessun APK proprietario,
peso del modello o chiave privata entra in Git. Le firme Google e F-Droid vengono
preservate. Le licenze di modello, voce e app vanno rispettate nella distribuzione;
per eSpeak-ng sono disponibili sorgenti e revisione nella procedura di build.

In cloud sono stati compilati i tre binari Android ARM64, con segmenti ELF a
16 KiB, i sorgenti Kotlin e le risorse con AAPT2 contro il framework Android 15.
È stata provata anche la generazione di un WAV italiano con eSpeak-ng sul PC.
I test coprono firme, cifratura, rotazione, conflitti, cancellazioni, importazioni,
file corrotti, conferme e scambio con il vero protocollo Python del PC. Gli archivi
dei test di packaging sono sintetici. Non equivalgono a una build Soong o a un
APK installato. Il proxy cloud blocca AOSP e i download dei modelli/F-Droid, e
lo spazio disponibile è insufficiente per AOSP: modello e dettatura non sono
stati eseguiti sul Motorola.

Gli aggiornamenti delle app passano da Play Store/F-Droid. Gli aggiornamenti
dell'immagine SoIA passano dal PC con immagini verificate; **OTA automatico e
rollback hardware non sono implementati in questa GSI generica**. Restano da
verificare SELinux, VINTF, spazio fisico, hardware e consumo sul Motorola. La
modalità «Ehi Nova» software può consumare sensibilmente più del futuro DSP;
l'obiettivo di consumo in standby non è una misura ottenuta sul telefono.

La base fissata serve al prototipo: prima di una release per uso quotidiano va
aggiornata a un livello di patch di sicurezza adeguato e verificata sul dispositivo.
