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
| **1 — Classificatore semantico** | riconosce frasi riformulate ("si sente troppo piano", "fammi sentire un po' di musica", "stacca il wifi") confrontandole con un catalogo di esempi; concetti (abbassa = riduci = più basso), regole come "troppo basso → alza", astensione su negazioni e parole ignote | **~0,3 ms** | ✅ `copilot/aios_copilot/semantic.py` (italiano e inglese) + `multilingual.py` (tutte le lingue, da calibrare) |
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

**Lingue.** Il livello 1 ha due codificatori in cascata:

- **integrato** (italiano e inglese, ~0,3 ms): concetti scritti a mano, il più veloce;
- **neurale multilingue** (tutte le lingue, ~10–50 ms su CPU): un modello di embedding
  servito da Ollama (candidati: `paraphrase-multilingual`, `granite-embedding:278m`,
  `bge-m3`), con gli esempi del catalogo pre-calcolati in cache. Il catalogo si può
  arricchire con traduzioni generate una volta sola dall'LLM locale.

Il comando `aios-copilot-setup` sceglie il modello sul dispositivo reale. Calibra le
soglie su metà delle frasi di prova, in 8 lingue con frasi-trappola e negazioni, con
due vincoli: zero frasi fuori tema eseguite e precisione ≥ 97%. Il risultato
dichiarato è quello misurato sull'altra metà. Le negazioni sono riconosciute anche
in spagnolo, francese, tedesco, portoghese, russo, cinese, giapponese e coreano.
*Stato:* la catena è verificata con un finto server Ollama; la qualità dei modelli
veri va misurata sul primo dispositivo (da questo ambiente non si possono scaricare).

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

### Conoscenza personale e privacy

Il copilota conosce i file dell'utente tramite un **indice locale** (SQLite FTS5 per
le parole, embedding per il significato), costruito a passi transazionali: uno
spegnimento a metà non corrompe nulla e il lavoro riprende dal punto esatto.

L'apprendimento avviene **a riposo** (`aios-learn`): utente inattivo o schermo
bloccato, alimentazione collegata, sistema scarico; priorità `SCHED_IDLE` e I/O
idle, cioè la CPU va all'apprendimento solo quando nessun altro la usa. Passi da
0,5 s: al ritorno dell'utente la pausa è immediata. Durante lo standby il
processore è spento: il processo resta congelato e riprende al risveglio, che
viene riconosciuto (differenza tra `CLOCK_BOOTTIME` e `CLOCK_MONOTONIC`) per
lasciare libero il computer.

**Il rischio vero non è il cloud.** Un'AI che (1) legge dati privati, (2) legge
contenuti di terzi (web, documenti ricevuti) e (3) può comunicare all'esterno può
essere manipolata da istruzioni nascoste in una pagina per far uscire dati, anche
se tutto gira in locale. Le difese di AIOS:

- percorsi mai letti (chiavi, password, browser, posta) e segreti rimossi dall'indice;
- cartelle escludibili a voce, con rimozione immediata dall'indice;
- **porta di uscita sorvegliata**: dopo che una conversazione ha letto dati privati,
  ogni azione verso l'esterno chiesta dal modello si ferma e mostra cosa uscirebbe,
  segnalando i frammenti presi dai file (nomi, codici, importi);
- contenuti web e dei file passati al modello come dati, mai come istruzioni;
- permessi 600 per indice e cronologia; cifratura del disco e sandbox delle app
  (che non possono leggere `~/.local/share/aios`) nell'immagine di sistema. Una
  cifratura applicativa non basterebbe: un programma con lo stesso utente ne
  leggerebbe comunque la chiave.

**Addestramento del modello.** Oggi l'apprendimento è memoria e catalogo personale
(le frasi dell'utente diventano esempi del livello 1). Il passo successivo è un
adattamento LoRA del modello piccolo sulle richieste dell'utente, eseguito a riposo
nello stesso pianificatore e con checkpoint frequenti.

### Catalogazione automatica

`organize.py` classifica a riposo documenti (per argomento), foto (per momento, da
EXIF), video, musica (da tag), app e giochi (da `.desktop`), download; le raccolte
sono virtuali (`~/Raccolte` con collegamenti) e il riordino reale avviene solo su
richiesta, con piano, conferma e registro per annullare.

### Temi

`themes.py` (temi come soli dati, palette con contrasto WCAG garantito, atmosfere,
estrazione dei colori da un'immagine, ritocchi, sfondi disegnati), `themeapply.py`
(GTK/libadwaita, GNOME, KDE, app di AIOS via `/theme.css`, tema precedente),
`thememarket.py` (indice firmato, pacchetti validati, temi «ispirati a» solo per uso
personale). Prossimi passi: interfaccia del market con anteprime e valutazioni,
sfondi generati dal modello di immagini quando installato.

### Copilota proattivo (prossima fase)

Non solo esecutore: un copilota che, conoscendo l'utente, **propone**.

- **Agenda e promemoria** ✅: date e orari in linguaggio naturale capiti in locale
  (`when.py`), ricorrenze, avvisi recuperati dopo lo standby, formato iCalendar;
  scadenze trovate nei documenti proposte nel **riepilogo del mattino** ✅ (mai
  aggiunte senza il sì dell'utente). Da fare: sincronizzazione CalDAV, email.
- **Organizzazione della giornata**: impegni, scadenze, file su cui si sta lavorando.
- **Consigli**: film, serie, cartoni, musica, software e giochi in base ai gusti,
  **filtrati sugli abbonamenti attivi** (Netflix, Spotify…), riconosciuti in locale.
  Vedi [DESIGN.md](DESIGN.md). I cataloghi (novità, uscite) si scaricano in forma generica e la scelta
  avviene in locale: il profilo dei gusti non lascia mai il dispositivo.
- **Dosaggio**: pochi suggerimenti, nei momenti giusti; "non mi interessa" è a sua
  volta un segnale da cui imparare; "cosa sai di me?" mostra e corregge il profilo.

### Modelli adatti al dispositivo

`hardware.py` legge le caratteristiche del dispositivo; `models.py` sceglie per ogni
capacità (testo, vista, dettatura, voce, significato, immagini, video) il modello più
completo compatibile con memoria, GPU/CPU e disco, e lo propone; `learning.DownloadTask`
lo scarica a riposo con pausa e ripresa; `engines.py` lo collega alle funzioni del
sistema (il copilota cambia modello, compaiono vista, voce, dettatura, immagini).

**Catalogo aggiornabile.** I modelli migliori cambiano di mese in mese, quindi
l'elenco non sta nel codice: il progetto AIOS pubblica un catalogo JSON firmato
(Ed25519, `modelcatalog.py`), scaricato una volta a settimana con una richiesta
uguale per tutti. È accettato solo con firma valida per una chiave fidata
(`/etc/aios/catalog-keys.d/`), versione più alta di quella in uso (niente ritorni
a cataloghi vecchi) e, per i file, impronte SHA-256 che vengono verificate dopo lo
scaricamento. Senza chiavi configurate vale il catalogo integrato. Ogni modello
riporta la licenza; l'impostazione «solo licenze aperte» esclude quelle con
condizioni.

**Il laboratorio AIOS** (servizio del progetto, da costruire) valuta i nuovi modelli
aperti sui compiti reali di AIOS (uso degli strumenti, italiano, resistenza alle
istruzioni nascoste, velocità per classe di hardware) e pubblica nel catalogo solo
quelli che superano le soglie, con il punteggio.

**Prova sul dispositivo** (`trial.py`): prima di adottare un nuovo modello di testo
se ne misura la velocità reale e la precisione su un insieme di compiti di AIOS;
lo si adotta solo se è almeno buono quanto l'attuale e abbastanza veloce, altrimenti
si scarta e si libera lo spazio. Il modello precedente resta: «torna al modello di
prima».

### Memoria compressa: modelli più grandi su dispositivi piccoli

Un modello linguistico, per ogni parola, legge tutti i suoi pesi dalla memoria: la
memoria limita sia *quale* modello entra sia *quanto* è veloce. AIOS comprime in tre
punti, sempre in base al dispositivo (`memory.py`, `models.py`):

- **Pesi del modello compressi (quantizzazione).** Il catalogo contiene varianti a
  4, 3 e 2 bit dello stesso modello. Un 14B a 3 bit entra in una GPU da 12 GB dove
  quello a 4 bit non entra; un 32B a 3 bit entra in 24 GB. La compressione toglie un
  po' di qualità, quindi ogni variante ha un punteggio atteso (`score`) e la
  **catena di prove** decide: se la variante scelta, provata sul dispositivo, è troppo
  lenta o meno precisa del modello attuale, viene scartata (non si ripropone) e AIOS
  prova da solo la successiva, mai sotto il modello già in uso. Senza GPU si
  escludono i modelli che richiedono di leggere più di 5 GB per parola: sarebbero
  troppo lenti per una conversazione.
- **Memoria della conversazione compressa** (KV cache a 8 bit, o a 4 bit sotto gli
  8 GB di RAM, con flash attention): contesti lunghi in metà o un quarto dello spazio.
  Impostata per il servizio Ollama con un file di systemd, insieme a una lunghezza
  di contesto adatta alla RAM.
- **RAM compressa** (zram con zstd, metà della RAM fino a 8 GB): le pagine delle app
  ferme restano in memoria compresse 2–4 volte invece di finire sul disco, e resta
  più memoria vera per il modello. Parametri del kernel adatti (swappiness alta,
  lettura di una pagina alla volta).

Le impostazioni di sistema si applicano solo su richiesta («ottimizza la memoria»),
con conferma e password di amministratore (pkexec); «quanta memoria ho» mostra lo
stato e quanto si sta risparmiando.

**Modelli a esperti (MoE, `moe.py`).** Un modello come Qwen3 30B-A3B ha 30 miliardi
di parametri ma per ogni parola ne usa circa 3: va veloce come un modello piccolo e
ragiona quasi come uno grande. Il catalogo indica per ogni modello a esperti quanti GB
si leggono per parola (`active_gb`); AIOS stima la velocità di ogni sistemazione e
sceglie la più veloce sopra le 4 parole al secondo:

| Modalità | Quando | Motore |
|---|---|---|
| gpu | tutto entra nella scheda video | Ollama |
| ram | tutto entra nella RAM (es. 32 GB senza GPU: ~20 parole/s) | Ollama |
| gpu+ram | attenzione sulla GPU, esperti in RAM | llama.cpp `--n-cpu-moe` |
| disco | gli esperti più usati in RAM, gli altri letti dal disco NVMe/SSD quando servono (mmap) | llama.cpp |

Per le ultime due Ollama non basta (rifiuta i modelli più grandi della RAM): AIOS
avvia `llama-server` come servizio utente (`aios-esperti.service`) direttamente sul
file GGUF già scaricato da Ollama, senza copie, e il copilota gli parla con l'API
OpenAI (`llm.LlamaServerClient`). La stima tiene conto che gli esperti non sono usati
tutti allo stesso modo; la velocità vera la misura la prova sul dispositivo, che
scarta il modello se è lento (e ferma il servizio). Tornando al modello di prima,
il servizio si ferma e la memoria si libera.

Ricerca futura del laboratorio AIOS: pesi compressi senza perdita, decompressi
direttamente durante il calcolo.

### Aggiornamenti del sistema

Previsti nell'immagine (non ancora costruita), con lo stesso schema:

- **sistema base immutabile** (ostree / Fedora Atomic): l'aggiornamento intero si
  scarica in background, a riposo e in carica, e si applica in modo atomico al
  riavvio successivo; mai un sistema "aggiornato a metà";
- **ritorno automatico** alla versione precedente se il nuovo sistema non si avvia
  correttamente (controlli all'avvio in stile greenboot), e sempre possibile a mano;
- app (Flatpak) e modelli AI (catalogo firmato) aggiornati separatamente;
- tutto firmato; il copilota avvisa e lascia scegliere il momento del riavvio, mai
  forzato mentre si lavora; gli aggiornamenti di sicurezza sono evidenziati.

### Telefono e PC (`mesh/`)

Il telefono e il PC si collegano da soli quando sono vicini (stessa rete):

- **Base: KDE Connect** (app per Android e iPhone, protocollo cifrato e maturo).
  L'abbinamento si conferma una volta sul telefono; poi il servizio `aios-telefono`
  vede ogni pochi secondi quali telefoni abbinati sono vicini, avvisa quando uno
  arriva o se ne va, e il copilota può farlo squillare o mandargli file e link.
- **File del PC dal telefono** (`mesh/files.py`): una pagina HTTPS in rete locale,
  accesa *solo* mentre un telefono abbinato è vicino (o durante un abbinamento).
  Abbinamento con un QR mostrato sullo schermo del PC: chi lo inquadra è davanti al
  PC; il codice vale una volta e 5 minuti (massimo 10 tentativi). Il telefono riceve
  una chiave personale, il PC ne conserva solo l'impronta e la può revocare
  («scollega il telefono»). Solo lettura, solo la cartella personale, mai file
  nascosti o esclusi dalla privacy, nessuna uscita con link simbolici; si scarica con
  link monouso di 2 minuti, e i file HTML non vengono mai aperti come pagine.
  La ricerca usa l'indice personale (anche nel contenuto dei documenti).
- **Chiamate dal PC** (`mesh/calls.py`): il PC fa da vivavoce Bluetooth del
  telefono (profilo HFP, ruolo hands-free, con PipeWire/WirePlumber e oFono). Una
  chiamata in arrivo apre una notifica con «Rispondi» / «Rifiuta», e il copilota
  capisce «rispondi» e «riaggancia».
- **Notifiche e SMS** (`mesh/messages.py`): tramite il demone KDE Connect (D-Bus) il
  copilota legge le notifiche del telefono e le riassume (messaggi delle persone,
  codici, chiamate, il resto raggruppato per app), legge gli SMS con i nomi della
  rubrica sincronizzata dal telefono, risponde ai messaggi (WhatsApp, Telegram… se
  l'app lo consente) e manda SMS. I **codici di verifica** vengono riconosciuti: il
  servizio propone una notifica con «Copia», e «copia il codice» li mette negli
  appunti. Notifiche e SMS sono dati privati (protezione dalle fughe del copilota);
  ogni invio chiede conferma, e un nome ambiguo nella rubrica non viene indovinato.
- **Delega AI dal telefono al PC** (`mesh/delegate.py`):
  - *cervello prestato*: sul telefono con AIOS il copilota resta quello del telefono,
    con i suoi strumenti, ma il ragionamento lo fa il modello del PC (`/api/modello`)
    quando è vicino. `HybridModel` torna al modello del telefono appena il PC non
    risponde e lo riprova dopo 30 secondi. Il certificato del PC è «fissato» al
    momento dell'abbinamento (l'impronta è nel QR): a chi si finge il PC non arriva
    nulla. L'abbinamento: `aios-telefono collega-pc <indirizzo del QR>`;
  - *«Chiedi al PC»* nella pagina del telefono: il copilota del PC risponde con un
    insieme ristretto di strumenti (`PHONE_ALLOWED`: file, posta, agenda, web,
    consigli); niente che cambi il PC da lontano (app, impostazioni, spegnimento,
    temi, riordino, schermo). Le conferme, per esempio per inviare una mail, si danno
    sul telefono.
- Il copilota parla con il servizio da un socket locale leggibile solo dall'utente.

Prossimi passi: identità dell'utente con una chiave principale per
tutti i dispositivi, sincronizzazione con CRDT cifrati end-to-end.

## Roadmap

| Fase | Obiettivo |
|---|---|
| **1 — Copilota** *(in corso; benvenuto conversazionale ✅)* | `aios-copilot` funzionante su qualsiasi Linux: ricerca web, installazione/avvio app, overlay grafico richiamabile da tastiera, motore di intenti veloce, classificatore semantico |
| 1b — Conoscenza personale *(in corso)* | indice dei file ✅, protezione dalle fughe di dati ✅, apprendimento a riposo ✅, agenda e promemoria ✅, riepilogo del mattino ✅, consigli personalizzati |
| 2 — Immagine PC | immagine immutabile con shell AIOS, copilota integrato, Bottles e Waydroid preinstallati |
| 3 — Mesh *(in corso: telefono↔PC ✅)* | collegamento tra i dispositivi dello stesso utente, delega AI dal telefono al PC |
| 4 — Mobile | immagine per 1–2 telefoni/tablet, input vocale |
| 5 — Ecosistema | SDK per esporre le funzioni delle app al copilota, memoria personale semantica |
