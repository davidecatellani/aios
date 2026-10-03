# AIOS — Architettura

AIOS è un sistema operativo grafico, unico per computer, tablet e telefoni, in cui
un **copilota AI locale** è il modo principale di interagire: si chiede in lingua
naturale ("installa un programma per montare video", "cerca gli orari dei treni per
Milano") e l'AI esegue, chiedendo conferma per le azioni importanti.

## Principi

1. **Linguaggio naturale prima di tutto.** Ogni funzione del sistema è raggiungibile
   chiedendola all'AI. Menu e impostazioni classiche esistono, ma sono il piano B.
2. **Copilota sempre a portata di mano.** Tasto `Super+Spazio` sul PC, pulsante
   fisso o gesto sul telefono, e in futuro una parola di attivazione vocale.
3. **AI locale e privata.** Il modello gira sul dispositivo. Internet si usa solo per
   le azioni che lo richiedono, come le ricerche e i download, e l'utente lo vede.
4. **Un solo sistema, tanti schermi.** La stessa base software e la stessa interfaccia
   adattiva su tutti i dispositivi.
5. **Nessuna azione distruttiva senza conferma.** Installazioni, rimozioni e modifiche
   di sistema mostrano sempre all'utente cosa sta per accadere.
6. **Tutto è reversibile.** Il sistema base è immutabile, con aggiornamenti atomici
   e rollback: se l'AI sbaglia un'installazione, si torna indietro.

## Strati

```
┌──────────────────────────────────────────────────────────────┐
│ Shell grafica adattiva (GTK4 + libadwaita)                   │
│   desktop · tablet · telefono    +  overlay del Copilota     │
├──────────────────────────────────────────────────────────────┤
│ Copilota AI (aios-copilot)                                   │
│   agente LLM ── strumenti: app · web · file · impostazioni   │
│                            · dispositivi collegati           │
├───────────────────────────┬──────────────────────────────────┤
│ Runtime AI locale         │ Mesh dispositivi utente          │
│ Ollama / llama.cpp        │ identità a chiavi, scoperta LAN, │
│ + indice semantico locale │ sync cifrato, delega di calcolo  │
├───────────────────────────┴──────────────────────────────────┤
│ Compatibilità applicazioni                                   │
│   Linux nativo (Flatpak, pacchetti) · Windows (Wine/Bottles) │
│   · Android (Waydroid)                                       │
├──────────────────────────────────────────────────────────────┤
│ Base immutabile Linux (Fedora Atomic / postmarketOS)         │
│   kernel, driver, systemd, Wayland, PipeWire, polkit         │
└──────────────────────────────────────────────────────────────┘
```

### Base del sistema

- **Kernel Linux.** È l'unica scelta realistica per supportare hardware reale e far
  girare le applicazioni Linux esistenti.
- **Immagine immutabile** (modello Fedora Atomic / ostree): il sistema base è in sola
  lettura e si aggiorna in modo atomico, mentre le applicazioni vivono separate in
  Flatpak. Così l'AI può installare software senza mai "rompere" il sistema.
- **Telefoni e tablet:** si parte da postmarketOS o Fedora Mobility, sui modelli già
  supportati (Pixel, OnePlus 6, PinePhone, tablet x86). I driver dei telefoni restano
  il collo di bottiglia, quindi il supporto si allarga un modello alla volta.

### Applicazioni

| Tipo | Come girano | Note |
|---|---|---|
| Linux | native, preferibilmente da Flathub | sandbox e permessi per app |
| Windows | Wine tramite Bottles | funzionano molte app; non quelle con anti-cheat o protezioni a livello kernel |
| Android | Waydroid | utile soprattutto su telefono e tablet |
| Web | browser / PWA | |

L'utente non deve sapere da dove arriva un'app: chiede "installa Photoshop" e il
copilota propone l'alternativa migliore (GIMP, Krita, Photopea) oppure il percorso
tramite Bottles, spiegando i limiti.

### Copilota AI

È un agente: un LLM locale con *tool calling* che usa **strumenti** ben definiti.
Ogni strumento dichiara se richiede conferma.

| Strumento | Esempio di richiesta | Conferma |
|---|---|---|
| `search_web` / `read_webpage` | "che tempo fa domani a Torino?" | no |
| `search_apps` | "c'è un programma per i PDF?" | no |
| `install_app` / `remove_app` | "installa VLC" | **sì** |
| `launch_app` | "apri il browser" | no |
| `open_location` | "apri la cartella Download" | no |
| `system_info` | "quanta memoria libera ho?" | no |
| *(prossimi)* impostazioni, file, rete, dispositivi collegati, automazioni | "attiva la modalità scura", "manda questa foto al PC" | dipende |

Le azioni privilegiate passano da **polkit**, quindi il sistema stesso fa da ultima
barriera anche se il modello sbagliasse.

Il dettaglio di come il copilota resta veloce anche senza GPU è nella sezione
successiva.

### Motore AI: veloce anche senza GPU

Il copilota tradizionale manda ogni frase a un grande modello linguistico. Su un
telefono o un PC senza GPU questo significa secondi di attesa per ogni comando.
AIOS parte da un'idea diversa: **essendo il sistema operativo, sa già quasi tutto**
(app installate, file, impostazioni, cosa c'è sullo schermo). Il modello linguistico
serve solo per la parte che il sistema davvero non sa.

Le richieste attraversano una cascata di livelli; ognuno risponde solo se è sicuro,
altrimenti passa al successivo:

| Livello | Cosa fa | Tempo tipico su CPU | Stato |
|---|---|---|---|
| **0 — Motore di intenti** | regole + conoscenza del sistema: "apri Firefox", "installa VLC", "apri i Download", "cerca …" | **~10 µs** | ✅ `copilot/aios_copilot/fastpath.py` |
| **1 — Classificatore semantico** | riconosce frasi riformulate ("si sente troppo piano", "fammi sentire un po' di musica", "stacca il wifi") confrontandole con un catalogo di esempi; concetti (abbassa = riduci = più basso), regole come "troppo basso → alza", astensione su negazioni e parole ignote | **~0,3 ms** | ✅ `copilot/aios_copilot/semantic.py` (italiano e inglese) |
| 2 — Modello linguistico piccolo | 0,5–3 miliardi di parametri quantizzati a 4 bit (o ternari, tipo BitNet), con output vincolato al formato delle azioni | 0,3–2 s | ✅ base (`qwen2.5:1.5b` via Ollama) |
| 3 — Modello grande | sul PC dell'utente, raggiunto tramite la mesh, o nel cloud se l'utente lo sceglie | variabile | futuro |

Il livello 2 ha un default piccolo perché su CPU conta la reattività. È proprio
grazie ai livelli 0–1, che gestiscono i comandi frequenti, che basta un modello piccolo.

**Qualità del livello 1, misurata.** Su frasi mai viste e scritte dopo la messa a
punto: precisione 95%, copertura 81%, nessun falso positivo su 12 frasi-trappola
("spegni la luce in cucina", "riavvia il router di casa", "blocca il numero di
Marco"). Sul banco di prova permanente (`copilot/tests/semantic_eval.py`):
precisione 100%, copertura 89%, 0 falsi positivi su 32. Quando non è sicuro il
livello 1 non agisce: la richiesta passa all'LLM.

**Lingue.** Il codificatore integrato conosce italiano e inglese; nelle altre
lingue si astiene e risponde l'LLM, che è multilingue ma più lento. Il prossimo passo
è un modello di embedding multilingue (es. `multilingual-e5-small`, ~10–30 ms su
CPU, già supportato tramite `AIOS_EMBED_MODEL`, soglie da calibrare) affiancato a
cataloghi tradotti automaticamente per le lingue principali.

**Vantaggi che ha solo un sistema operativo** (e che un'app non può avere):

- **Modello sempre caricato.** Viene caricato all'avvio, non viene mai scaricato dalla
  memoria e i pesi sono condivisi tra i processi: niente attese di caricamento. ✅ (`keep_alive`)
- **Prompt pre-elaborato.** Istruzioni e strumenti vengono elaborati una volta sola e
  la loro cache viene riusata; senza GPU, leggere il prompt è il costo maggiore. ✅ (warmup all'avvio)
- **Contesto senza fatica.** Il sistema passa all'AI direttamente l'app attiva, la
  selezione e gli appunti, invece di farglieli "indovinare" con prompt lunghi.
- **Priorità dello scheduler.** Le richieste dell'utente ottengono i core più veloci
  e la memoria con pagine grandi (huge pages).
- **Indice sempre aggiornato.** File, app e impostazioni vengono indicizzati mentre
  cambiano (inotify), non al momento della domanda.
- **Esecuzione anticipata.** Mentre l'utente scrive, il livello 0–1 prevede l'azione
  e prepara il necessario.
- **Acceleratori.** NPU e unità vettoriali dei SoC dei telefoni, quando disponibili.

**Verso un modello "nostro".** Addestrare da zero un modello generalista costa milioni
e non serve. La strada percorribile è un modello piccolo **specializzato sulle
azioni di AIOS**, ottenuto con fine-tuning e distillazione da un modello grande,
usando come dati le richieste reali (anonime e con consenso) e il catalogo delle
azioni del sistema. Un modello da 1 miliardo di parametri addestrato su questo
compito può battere un modello generalista dieci volte più grande.

### Mesh dei dispositivi

- **Identità:** ogni utente ha una chiave principale, e ogni dispositivo riceve una
  chiave firmata da quella.
- **Scoperta:** mDNS in LAN, Bluetooth LE di prossimità, relay opzionale da remoto.
- **Funzioni:** clipboard condivisa, invio file, notifiche unificate, handoff delle
  app, telefono come telecomando/webcam, delega di calcolo AI.
- **Punto di partenza:** il protocollo KDE Connect, già maturo e compatibile con
  Android; la sincronizzazione dei dati sarà basata su CRDT con cifratura end-to-end.

## Roadmap

| Fase | Obiettivo |
|---|---|
| **1 — Copilota** *(in corso)* | `aios-copilot` funzionante su qualsiasi Linux: ricerca web, installazione/avvio app, overlay grafico richiamabile da tastiera, motore di intenti veloce, classificatore semantico |
| 2 — Immagine PC | immagine immutabile con shell AIOS, copilota integrato, Bottles e Waydroid preinstallati |
| 3 — Mesh | collegamento tra i dispositivi dello stesso utente, delega AI dal telefono al PC |
| 4 — Mobile | immagine per 1–2 telefoni/tablet, input vocale |
| 5 — Ecosistema | SDK per esporre le funzioni delle app al copilota, memoria personale semantica |
