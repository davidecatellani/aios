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
   Primo giro (Release `velocita-1`, Xeon con 2 core veri e 4 thread): Ollama com'è oggi 67,5 s di attesa media
   e 17,1 minuti per il giro; Ollama con flash attention e cache a 8 bit più lento (21,7 min); llama-server con un
   posto 13,9 min, con un posto per tipo di lavoro 13,1 min (le istruzioni fisse lette una volta: metà dei token
   letti), con 4 thread 11,3 min (1,5 volte più veloce di oggi, 6,4 parole al secondo invece di 4,8). Ma
   llama-server così usa 9,3 GB di RAM (la memoria dei prompt passati è di 8 GB in partenza): troppo per un PC da
   8 GB. La bozza non è partita: llama.cpp prende lo 0.8B per una testa MTP (va detto `--spec-type draft-simple`).
   Secondo giro: RAM limitata, bozza, testa MTP del 4B, previsione a n-grammi.
   Secondo giro (Release `velocita-2`, AMD EPYC con 2 core veri e 4 thread, più lento a leggere: 24 token al
   secondo): una domanda normale a Nova aspetta **1,5 s con llama-server invece di 45,7 s con Ollama**, e si scrive
   a 9 parole al secondo invece di 6,4. La bozza (0.8B), la testa MTP e gli n-grammi sul processore rallentano
   (6,6, 7,8 e 7,2 parole al secondo, pur con 88% e 79% delle parole della bozza accettate): non si usano. La RAM:
   il 4B a 4 bit 3,0 GB, la memoria della conversazione ~100 KB per token (800 MB per posto da 8192 token), le copie
   in RAM delle istruzioni già lette 300-480 MB l'una. Terzo giro: il 4B IQ4_XS (2,7 GB), due posti su un'unica
   memoria da 8192 token, memoria a 8 bit o no, niente copie in RAM.
   Terzo giro (Release `velocita-3`, AMD EPYC 9V74 con 2 core veri e 4 thread): il 4B IQ4_XS su llama-server con
   due posti su un'unica memoria da 8192 token e niente copie in RAM occupa **3,4 GB in tutto**; una domanda normale
   a Nova aspetta **1,0-1,3 s** (la conversazione resta nel suo posto anche mentre il sottofondo lavora nell'altro);
   la posta di tre settimane 50 s, il codice 68 s. La memoria a 8 bit risparmia solo 0,1 GB e legge il 30% più
   piano: resta a 16 bit. È la configurazione di aios-modello. Con il nucleo (0.8B, ~0,9 GB) l'AI tiene ~4,3 GB su
   un PC da 8: ~3,7 GB restano a sistema e programmi (il nucleo a 4 bit ne libererebbe altri ~0,3, da misurare).
   Qualità del 4B IQ4_XS (Release `procedure-4`, stesse prove delle procedure di `procedure-3` col Q4 di Ollama):
   a fasi posta 100% (uguale), preventivo 100% (uguale), spese 86% (79% col Q4): regge, resta in aios-modello.
   Modelli a esperti (Release `velocita-4`, stessa macchina e impostazioni da 8 GB del 4B IQ4_XS): scrivono 3 volte
   più in fretta (LFM2-8B-A1B 26 parole al secondo, Granite 4.0 H Tiny 23, il 4B 7,8) ma leggono poco più in fretta
   (posta 40-45 s contro 48) e occupano **7,9-9,0 GB** contro 3,4: su un PC da 8 GB no. LFM2 non usa gli strumenti
   (si inventa il meteo) e al programmatore risponde in inglese; Granite usa gli strumenti giusti e risponde in 0,5 s
   alle domande normali, ma nella posta sbaglia le date: da misurare per i PC da 16 GB con le prove di qualità.
2. **I modi che vincono, in SoIA**; più la protezione della memoria del modello (limiti di systemd perché il kernel
   non lo scarichi quando si apre il browser) e la cache del contesto salvata su disco per le istruzioni fisse.
   Misurati prima e dopo su un i3 vero.
3. **Mappa degli esperti con un indice per significato** (l'idea dei percorsi dell'informazione): per un modello a
   esperti più grande della RAM, si registra quali esperti usa ogni tipo di richiesta, si cercano le richieste
   simili e si caricano in anticipo dal disco i loro esperti (`posix_fadvise` sui pezzi del file GGUF, senza
   toccare llama.cpp). Con 8 GB e un SSD SATA un 30B-A3B resta lento (circa 1,7 GB da leggere per parola se gli
   esperti non sono in memoria): ha senso con modelli a esperti più piccoli o con un NVMe. Prima prova: quanto
   richieste simili usano esperti simili.
4. **I livelli dei dati** (bronze → silver → gold, come un datalake, in SQLite): il lavoro lento del modello si
   sposta da *mentre l'utente aspetta* a *mentre non c'è*. Primo caso, la posta (`aios_copilot/mail/livelli.py`):
   bronze le mail, silver i fatti di ogni mail (tipo, chi, cosa, entro quando: le mail di massa li decide il codice,
   le altre il modello in sottofondo, solo in carica, cedendo il passo all'utente), gold le cose da fare in ordine di
   scadenza, composte dal codice. Nova: «cosa mi chiedono nelle mail?» (strumento `mail_todo`) e il riepilogo della
   posta. Misura: `.github/workflows/livelli.yml`. Risultato (Release `livelli-1`, Qwen3.5 4B su CPU, 4 caselle di
   20 mail con 5 cose da fare): bronze 89 s di attesa, 1 casella su 4 tutta giusta (le voci ci sono quasi sempre,
   l'ordine per scadenza no); gold risposta istantanea, 4 caselle su 4 giuste, 87 s di lavoro in sottofondo per
   casella (8 mail lette dal modello, 12 dal codice). Parquet e Iceberg no: sono fatti per tabelle enormi lette da
   più motori; per un utente solo basta SQLite.
   Secondo caso, documenti e bollette (`aios_copilot/bollette.py`): bronze i documenti nell'indice dei file,
   silver i fatti di ogni documento (tipo, servizio, emittente, data, scadenza, totale, addebito automatico; quelli
   che non sono bollette li scarta il codice, gli altri li legge il modello quando il PC è libero e il codice
   controlla che importo e scadenza ci siano davvero nel documento), gold le bollette da pagare in ordine di
   scadenza e quanto si è speso per un servizio in un anno. Nova: «che bollette devo pagare?», «quanto ho speso di
   luce quest'anno?» (strumenti `bills_to_pay`, `bills_spent`). Il modello di sottofondo si interrompe da solo se
   l'utente parla con Nova (`precedenza.BackgroundModel`), per la posta e per i documenti.
   Risultato (Release `livelli-3`, 3 cartelle di 25 documenti con 12 bollette): bronze 178 s di attesa per due
   domande, 0 cartelle su 3 tutte giuste (58% dei controlli: bollette dimenticate, ordine sbagliato, già scadute
   messe tra quelle da pagare); gold risposta istantanea, 96% dei controlli e in realtà tutte giuste (l'unico
   errore era della prova: una rata TARI segnata come addebito automatico senza che il documento lo dicesse),
   150 s di lavoro in sottofondo per cartella. Il primo giro (`livelli-2`, 58%) aveva mostrato che il 4B a volte
   scrive JSON non valido: ora si legge con tolleranza e il codice ricava tipo, fornitore e rata dal testo.
5. **Neuroni attivi nei modelli densi** (Deja Vu, PowerInfer): solo se il resto non basta; serve un motore apposito
   e i modelli di oggi ne accendono troppi.
