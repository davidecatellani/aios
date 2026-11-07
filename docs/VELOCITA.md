# Velocità del modello su un PC modesto

Obiettivo: un i3 con 8 GB di RAM (2 core veri, 4 thread, memoria DDR3/DDR4, niente scheda video dedicata) che usa
Qwen3.5 4B senza attese lunghe.

## Dove va il tempo

- **Leggere il contesto** (calcolo): le richieste di SoIA portano 2-3 mila token di istruzioni, strumenti, codice o
  posta. Su un processore di questo tipo sono decine di secondi prima della prima parola: per SoIA è la parte più
  lenta.
- **Scrivere** (memoria): per ogni parola si rilegge tutto il modello dalla RAM. Con circa 20 GB/s e 2,5 GB di
  modello il tetto è sulle 8 parole al secondo.
- **Il modello cacciato dalla RAM**: con 8 GB, aprendo il browser il kernel scarica pezzi del modello; la richiesta
  dopo li rilegge dal disco.

## In che ordine

1. **La misura** (`.github/workflows/velocita.yml`, `copilot/addestramento/velocita_prova.py`): 12 richieste vere
   (Nova con gli strumenti, il programmatore, la posta) alternate, con modi diversi di far girare lo stesso modello:
   Ollama com'è oggi, Ollama con flash attention e cache a 8 bit, llama-server con un posto solo, con un posto per
   tipo di lavoro (le istruzioni fisse si leggono una volta), con la bozza (lo 0.8B del nucleo scrive in anticipo,
   il 4B controlla), con 4 thread invece di 2. Le macchine di GitHub hanno 2 core veri e 4 thread come un i3, ma la
   memoria è più veloce: contano i rapporti tra i modi.
2. **I modi che vincono, in SoIA**; più la protezione della memoria del modello (limiti di systemd perché il kernel
   non lo scarichi quando si apre il browser) e la cache del contesto salvata su disco per le istruzioni fisse.
   Misurati prima e dopo su un i3 vero.
3. **Mappa degli esperti con un indice per significato** (l'idea dei percorsi dell'informazione): per un modello a
   esperti più grande della RAM, si registra quali esperti usa ogni tipo di richiesta, si cercano le richieste
   simili e si caricano in anticipo dal disco i loro esperti (`posix_fadvise` sui pezzi del file GGUF, senza
   toccare llama.cpp). Con 8 GB e un SSD SATA un 30B-A3B resta lento (circa 1,7 GB da leggere per parola se gli
   esperti non sono in memoria): ha senso con modelli a esperti più piccoli o con un NVMe. Prima prova: quanto
   richieste simili usano esperti simili.
4. **Neuroni attivi nei modelli densi** (Deja Vu, PowerInfer): solo se il resto non basta; serve un motore apposito
   e i modelli di oggi ne accendono troppi.
