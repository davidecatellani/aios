# AIOS Copilot

L'assistente AI locale di AIOS. È pensato per essere veloce anche **senza GPU**:

1. i comandi comuni ("apri Firefox", "installa VLC", "apri i Download", "cerca …")
   vengono capiti dal **motore di intenti** in circa 10 µs, senza usare il modello AI;
2. le frasi riformulate ("si sente troppo piano", "stacca il wifi", "fammi vedere le
   mie foto") vengono riconosciute dal **classificatore semantico** in ~0,3 ms
   (italiano e inglese);
3. tutto il resto va a un modello linguistico piccolo in esecuzione sul tuo computer
   (tramite [Ollama](https://ollama.com)), tenuto sempre in memoria e preparato
   all'avvio.

Dettagli in [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md#motore-ai-veloce-anche-senza-gpu).

| Strumento | Cosa fa | Chiede conferma |
|---|---|---|
| `search_web`, `read_webpage` | cerca su internet e legge le pagine | no |
| `search_apps` | cerca app su Flathub e nei pacchetti di sistema | no |
| `install_app`, `remove_app` | installa/disinstalla (Flatpak, oppure apt tramite polkit) | **sì** |
| `launch_app` | apre un'applicazione | no |
| `open_location` | apre file, cartelle e siti | no |
| `system_info` | CPU, memoria, disco | no |
| `set_volume`, `set_brightness`, `set_theme`, `set_radio` | audio, luminosità, tema scuro/chiaro, Wi-Fi e Bluetooth | no |
| `media_control`, `take_screenshot`, `lock_screen` | musica, screenshot, blocco schermo | no |
| `power` | sospensione, spegnimento, riavvio | **sì** |

## Prova

```bash
# 1. Modello locale con supporto agli strumenti (piccolo: va bene anche senza GPU)
ollama pull qwen2.5:1.5b-instruct

# 2. Interfaccia grafica GTK4 (Debian/Ubuntu; su Fedora: python3-gobject gtk4)
sudo apt install python3-gi gir1.2-gtk-4.0

# 3. Copilota
cd copilot && pip install -e .
aios-copilot                               # finestra grafica
aios-copilot "installa un lettore video"   # oppure dal terminale
```

**Richiamarlo con un tasto:** nelle impostazioni della tastiera del tuo desktop
aggiungi una scorciatoia personalizzata `Super+Spazio` → `aios-copilot`. Se la
finestra è già aperta, torna in primo piano. `Esc` la nasconde.

### Configurazione

| Variabile | Default | |
|---|---|---|
| `AIOS_MODEL` | `qwen2.5:1.5b-instruct` | qualsiasi modello Ollama con tool calling; su PC potenti ad es. `qwen2.5:7b-instruct` |
| `AIOS_OLLAMA_URL` | `http://localhost:11434` | anche un altro PC di casa |
| `AIOS_EMBED_MODEL` | *(vuoto → classificatore integrato)* | modello di embedding Ollama per il livello 1 (sperimentale) |
| `AIOS_SEARXNG_URL` | *(vuoto → DuckDuckGo)* | istanza [SearXNG](https://docs.searxng.org) per ricerche private |

## Test

```bash
pip install -e '.[test]' && pytest
python tests/semantic_eval.py          # qualità del livello 1
python -m aios_copilot.semantic        # prova interattiva del livello 1
```
