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

**Modelli:** su PC un modello da 7–14 miliardi di parametri con tool calling
(famiglie Qwen, Llama, Mistral) tramite Ollama. Su telefono un modello da 1–4
miliardi; le richieste pesanti possono essere delegate al PC dell'utente tramite la
mesh, senza passare dal cloud. Un modello cloud resta un'opzione esplicita, scelta
dall'utente.

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
| **1 — Copilota** *(in corso)* | `aios-copilot` funzionante su qualsiasi Linux: ricerca web, installazione/avvio app, overlay grafico richiamabile da tastiera |
| 2 — Immagine PC | immagine immutabile con shell AIOS, copilota integrato, Bottles e Waydroid preinstallati |
| 3 — Mesh | collegamento tra i dispositivi dello stesso utente, delega AI dal telefono al PC |
| 4 — Mobile | immagine per 1–2 telefoni/tablet, input vocale |
| 5 — Ecosistema | SDK per esporre le funzioni delle app al copilota, memoria personale semantica |
