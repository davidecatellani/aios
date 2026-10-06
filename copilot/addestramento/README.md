# Adattatori del nucleo

Un solo modello piccolo in memoria (Qwen3.5 0.8B) con un adattatore LoRA per compito, al posto di più
modelli separati (vedi `aios_copilot/nucleo.py`):

| adattatore | cosa fa |
|---|---|
| `smistamento` | l'ambito della frase (agenda, posta, file…) e l'azione da fare |
| `campi` | i valori dell'azione (cosa, quando…) in JSON |
| `documenti` | legge bollette, scontrini, avvisi (flusso **Vista del nucleo**, con il proiettore delle immagini) |
| `schermate` | controlla una pagina di SoIA dopo una personalizzazione: l'ora delle lancette, testi sovrapposti, tagliati, poco leggibili o fuori schermo (flusso **Schermate del nucleo**) |

Si preparano con il flusso **Adattatori del nucleo** su GitHub Actions (avvio a mano): prepara le frasi
(`dati.py`, da `frasi.py` e dagli strumenti veri di Nova), addestra (`addestra.py`), converte in GGUF con
llama.cpp, misura il risultato (`valuta.py`) e pubblica tutto come `adattatori-N`. La costruzione
dell'immagine prende l'ultima pubblicazione: se non c'è, Nova usa Tev1 come prima.

Per migliorare Nova: aggiungere a `frasi.py` le frasi capite male e rilanciare il flusso.

Per le schermate: `schermate.py` disegna pagine vere di SoIA (persona inventata di `aios_copilot/anteprima.py`)
con orologi a lancette e guasti messi apposta, quindi le risposte giuste si sanno sempre; `valuta_schermate.py`
misura con e senza adattatore. Per insegnare un guasto nuovo: aggiungerlo a `INJECT_JS` e rilanciare il flusso.
