# SoIA per telefono: compilazione dell'immagine

SoIA per telefono è **Android open source** con sopra Nova e le scelte di SoIA
(energia, tema, collegamento al PC). Due basi:

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
phone/scripts/llama-android.sh            # llama.cpp per Nova
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

- Struttura, configurazione, app Nova, script e catalogo sono scritti; catalogo e
  installatore sono provati con i test (`copilot/tests/test_phone_build.py`).
- **Non ancora compilato**: qui non ci sono i sorgenti di Android (centinaia di GB).
  La prima compilazione vera farà emergere correzioni (Kotlin, SELinux, nomi di
  rilascio), normali per un primo giro.
- Nova su Android oggi: assistente di sistema, conversazione, «Chiedi al PC», modello
  locale su richiesta, lavori a basso consumo, collegamento al primo avvio. Da portare
  dal PC: livelli veloci (riconoscimento immediato delle frasi), identità e
  sincronizzazione firmata, decisioni di energia (energy.py), «Ehi Nova» sul DSP audio.
