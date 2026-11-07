# SoIA per telefono: compilazione dell'immagine

Il [prototipo integrato](PROTOTIPO.md) include Nova, AI e voce offline, dati cifrati,
collegamento al PC, KDE Connect, **Play Store e servizi Google**. Si prepara e
compila sul PC con un solo comando:

```bash
bash phone/scripts/pc-build.sh prototype
```

SoIA per telefono è **Android open source** con sopra Nova e le scelte di SoIA
(energia, tema, collegamento al PC). Due basi:

Per una versione comune a più marche stiamo sviluppando la **GSI Android 16 ARM64**:
vedi [configurazione, requisiti e limiti](GSI.md). È sperimentale e non ancora compilata.

Per il PC SoIA/Fedora con 32 GB di RAM e disco da 1 TB è disponibile la
[procedura con Podman e workflow manuale GitHub](PC-BUILD.md), con controlli,
download e compilazione separati.

| Base | Per | Perché |
|---|---|---|
| **AOSP** | Google Pixel, GSI (Motorola, Samsung, Oppo…) | il sistema di riferimento; sui Pixel l'avvio resta verificato con la chiave di SoIA |
| **LineageOS** | telefoni con supporto ufficiale LineageOS, es. **Redmi Note 9 Pro (miatoll)** | file hardware già mantenuti per centinaia di modelli: chiamate, fotocamera, sensori funzionano |

SoIA non modifica la base: `scripts/prepara.sh` collega nei sorgenti
`vendor/aios` (configurazione) e `packages/apps/Nova` (l'app di sistema); su LineageOS
anche `vendor/extra/product.mk`, che LineageOS include da sé.

## Cosa c'è

```
dispositivi.json              bersagli: base, ramo, prodotto, codici dei telefoni, file prodotti
vendor/aios/
  config/common.mk            Nova, sovrapposizione del framework, servizio di energia, SELinux
  config/energy.mk            obiettivo di consumo in standby (1%/ora)
  products/                   prodotti AOSP: aios_gsi_arm64, aios_shiba (Pixel 8)
  overlay/AiosFrameworkOverlay  tema scuro (OLED), Doze profondo, 60 Hz predefiniti
  init/aios.rc, bin/          valori di energia al primo avvio (risparmio adattivo, standby app, freezer)
  sepolicy/                   regole per il servizio di energia
packages/apps/Nova/           app di sistema (Kotlin, senza librerie esterne):
  assistant/                  Nova come assistente (pressione lunga del tasto di accensione)
  pc/PcBridge.kt              collegamento al PC con certificato fissato (come mesh/delegate.py)
  energy/                     lavoro in sottofondo solo con JobScheduler (in carica, Wi-Fi, fermo)
  llm/LocalModel.kt           llama.cpp avviato solo quando serve, spento dopo 60 s
  setup/RestoreActivity.kt    primo avvio: «Ripristina dal computer»
scripts/
  prepara.sh BERSAGLIO        scarica i sorgenti e collega SoIA
  llama-android.sh            compila llama.cpp per Android con l'NDK
  compila.sh BERSAGLIO        compila
  chiavi.sh SORGENTI          crea le chiavi (una volta, poi offline)
  firma.sh BERSAGLIO          firma e prepara i file in uscita/
  misura-batteria.sh [ore]    consumo in standby rispetto all'obiettivo
```

## Come si compila (es. Redmi Note 9 Pro)

Serve un PC Linux x86_64 con **almeno 300 GB liberi, 32–64 GB di RAM**, `repo`,
`git`, `python3` e l'NDK di Android. La prima compilazione richiede diverse ore.

```bash
phone/scripts/prepara.sh miatoll          # sorgenti di LineageOS 22.2 + SoIA
python3 phone/scripts/prepara-prototipo.py --cache ~/.cache/aios-prototype
phone/scripts/llama-android.sh            # llama.cpp per Nova
phone/scripts/voce-android.sh             # Whisper, eSpeak-ng e dati della voce
phone/scripts/chiavi.sh ~/aios-android/lineage-lineage-22.2   # solo la prima volta
phone/scripts/compila.sh miatoll
phone/scripts/firma.sh miatoll
aios-catalogo-telefoni --bersaglio miatoll --cartella phone/uscita/miatoll \
    --url https://<dove-pubblichi>/0.1/miatoll --chiave ~/.aios-chiavi/catalogo.pem --catalogo telefoni.json
```

Pubblicati `telefoni.json` e `telefoni.json.sig` insieme ai file, l'installatore
(`aios-installatore`, «Nova, installa SoIA sul telefono») li trova, verifica firma e
impronte, e per un Redmi Note 9 Pro (che con MIUI si presenta come «joyeuse») sceglie
l'immagine dedicata: recovery di SoIA e sistema via `adb sideload`.

Per il miatoll serve prima il firmware MIUI minimo indicato dal wiki di LineageOS
(pagina «fw_update»): lo controlleremo nell'installatore.

## Stato

Il primo dispositivo scelto per il prossimo sviluppo è **Motorola Edge 50 Neo**:
vedi [dati necessari, percorso e limiti](EDGE50NEO.md). La compatibilità della GSI
su questo modello non è ancora verificata.

- Struttura, configurazione, app Nova, script e catalogo sono scritti; catalogo e
  installatore sono provati con i test (`copilot/tests/test_phone_build.py`).
- **Immagine Android completa non ancora compilata**: qui non ci sono i sorgenti
  di Android (centinaia di GB). In cloud sono stati compilati i sorgenti Kotlin
  e le risorse di Nova, oltre ai tre motori nativi ARM64 con NDK r28c (segmenti ELF
  allineati a 16 KiB). La build Soong completa, SELinux e l'avvio sul telefono
  restano da verificare.
- Funzioni e condizioni del prototipo sono descritte in [PROTOTIPO.md](PROTOTIPO.md):
  voce offline, comandi tipizzati, identità, sincronizzazione, dati, PC e app incluse.
  «Ehi Nova» usa una modalità software facoltativa; DSP e OTA automatici restano
  fuori da questa GSI generica.

## Verificare Nova senza compilare tutta Android

Conferme, allegati, crittografia, sincronizzazione e importazioni hanno test Kotlin
eseguibili sul JVM e vettori prodotti dal vero protocollo Python del PC. Servono
Java, Kotlin (verificato con 2.1.20) e `org.json:json:20240303`, fuori dal repository:

```bash
AIOS_KOTLINC=/percorso/kotlinc/bin/kotlinc \
AIOS_JSON_JAR=/percorso/json-20240303.jar \
bash phone/scripts/test-nova.sh
```

Con `AIOS_ANDROID_JAR` impostato al framework Android, lo stesso script compila
anche tutti i sorgenti Kotlin dell'app (verificato con
`org.robolectric:android-all:15-robolectric-12650502`). Questo controllo risolve gli
identificativi `R` con un file temporaneo; non produce un APK, non verifica le
risorse con AAPT né sostituisce la build Soong, l'emulatore o le prove hardware.

Dal componente PC, i test delle conferme, del collegamento e del controllo USB:

```bash
cd copilot
AIOS_OLLAMA_URL=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost \
  python -m pytest -q tests/test_phone_confirmation.py tests/test_delegate.py tests/test_phone_probe.py
```
