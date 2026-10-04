# Adattatori del nucleo

Un solo modello piccolo in memoria (Qwen3.5 0.8B) con un adattatore LoRA per compito, al posto di più
modelli separati (vedi `aios_copilot/nucleo.py`):

| adattatore | cosa fa |
|---|---|
| `smistamento` | l'ambito della frase (agenda, posta, file…) e l'azione da fare |
| `campi` | i valori dell'azione (cosa, quando…) in JSON |

Si preparano con il flusso **Adattatori del nucleo** su GitHub Actions (avvio a mano): prepara le frasi
(`dati.py`, da `frasi.py` e dagli strumenti veri di Nova), addestra (`addestra.py`), converte in GGUF con
llama.cpp, misura il risultato (`valuta.py`) e pubblica tutto come `adattatori-N`. La costruzione
dell'immagine prende l'ultima pubblicazione: se non c'è, Nova usa Tev1 come prima.

Per migliorare Nova: aggiungere a `frasi.py` le frasi capite male e rilanciare il flusso.
