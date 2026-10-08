# Adattatori del nucleo

Un solo modello piccolo in memoria (Qwen3.5 0.8B) con un adattatore LoRA per compito, al posto di più
modelli separati (vedi `aios_copilot/nucleo.py`):

| adattatore | cosa fa |
|---|---|
| `smistamento` | l'ambito della frase (agenda, posta, file…) e l'azione da fare |
| `campi` | i valori dell'azione (cosa, quando…) in JSON |
| `documenti` | legge bollette, scontrini, avvisi (flusso **Vista del nucleo**, con il proiettore delle immagini) |
| `verifica` | il controllo di qualità delle personalizzazioni: dalla richiesta e da cosa è cambiato nella pagina, misurato prima e dopo (`aios_copilot/cambiamenti.py`), dice se la modifica è riuscita e cosa non torna; solo testo (flusso **Verifica del nucleo**) |

Si preparano con il flusso **Adattatori del nucleo** su GitHub Actions (avvio a mano): prepara le frasi
(`dati.py`, da `frasi.py` e dagli strumenti veri di Nova), addestra (`addestra.py`), converte in GGUF con
llama.cpp, misura il risultato (`valuta.py`) e pubblica tutto come `adattatori-N`. La costruzione
dell'immagine prende l'ultima pubblicazione: se non c'è, Nova usa Tev1 come prima.

Per migliorare Nova: aggiungere a `frasi.py` le frasi capite male e rilanciare il flusso.

Per la verifica: `verifiche.py` fa modifiche vere su pagine di SoIA (persona inventata di `aios_copilot/anteprima.py`),
apposta bene o male (colore, posizione, dimensione, testo, elementi tolti o aggiunti, angoli, bordi, tema scuro,
più a volte un danno: testi sovrapposti, tagliati, illeggibili, fuori schermo, duplicati, pagina rotta), quindi la
risposta giusta si sa sempre. Il modello non guarda immagini: legge le righe misurate («il riquadro «Meteo»: sfondo da
bianco a rosso»). Con le immagini lo 0.8B non ce la faceva (prove 3 e 4: 33% e 56% di giudizi giusti); l'orologio con
le lancette resta al modello che vede. `valuta_verifiche.py` misura con e senza adattatore. Per insegnare una
modifica nuova: aggiungerla a `APPLY_JS` e rilanciare il flusso.
