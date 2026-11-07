# Procedure: lavori ripetuti fatti a fasi (progetto)

Le «procedure» sono i lavori che l'utente fa spesso e sempre allo stesso modo: il resoconto delle spese del mese,
il preventivo per un cliente, il riassunto della posta della settimana, la newsletter dell'associazione. Nova li
esegue a fasi, e ogni fase scrive un documento normale che l'utente può aprire e correggere prima della fase dopo.

Il metodo viene da *Interpretable Context Methodology* (ICM, Van Clief e McDermott, arXiv 2603.16021, 2026):
una procedura è una cartella, le fasi sono sottocartelle numerate, ogni fase ha un contratto scritto in un file di
testo, e il modello riceve solo il contesto della fase in cui si trova. Niente framework: le cartelle e i file
sono lo stato del lavoro.

Perché in SoIA:

- **per i modelli locali conta più che per quelli grandi**: una fase riceve 2–8 mila token invece di tutto il
  lavoro insieme (40 mila e più). È lo stesso principio che ha portato la verifica delle personalizzazioni dal
  56% al 99%: il codice prepara, il modello decide solo quello che serve;
- **l'utente vede e corregge tutto**: ogni passaggio è un documento nella sua cartella, non un ragionamento
  nascosto;
- **non serve programmare**: per cambiare una procedura si cambia un testo, o lo si chiede a Nova.

Dove **non** si usa: comandi a voce, risposte veloci, smistamento. Sono lavori in tempo reale, il caso che il
paper stesso esclude. Lì Nova resta com'è.

## Com'è fatta una procedura

Le procedure stanno in `~/Procedure/`, una cartella per procedura, con i **nomi originali di ICM**: così una cartella
fatta per ICM funziona in SoIA e viceversa, senza traduzioni. Il contenuto dei file è in italiano.

```
~/Procedure/Resoconto spese/
├── CLAUDE.md                 identità: cos'è, a cosa serve, dove si trovano le cose     (livello 0)
├── CONTEXT.md                quale fase fa cosa, quando parte                           (livello 1)
├── stages/
│   ├── 01_raccolta/
│   │   ├── CONTEXT.md        il contratto della fase                                    (livello 2)
│   │   ├── references/       regole di questa fase                                      (livello 3)
│   │   └── output/           quello che la fase ha prodotto in questo giro              (livello 4)
│   ├── 02_conti/
│   └── 03_resoconto/
├── _config/                  regole che valgono sempre (stile, categorie…)              (livello 3, «la fabbrica»)
├── shared/                   materiale comune alle fasi                                 (livello 3)
├── setup/questionnaire.md    le domande per preparare la procedura
└── .nova/                    stato del giro, risultati originali, correzioni            (nascosta, solo SoIA)
```

Il contratto di una fase (`CONTEXT.md` della fase) ha le tre sezioni di ICM più tre di SoIA, tutte facoltative
tranne le prime tre:

```markdown
## Inputs
- Layer 4 (working): ../01_raccolta/output/
- Layer 3 (reference): ../../_config/categorie.md

## Process
Somma le spese per categoria e confrontale con il mese prima.

## Outputs
- conti.md -> output/

## Tools
- calculate

## Verify
- contiene: Totale
- ogni categoria di categorie.md compare nei conti

## Review
no
```

- **Inputs**: solo questi file entrano nel contesto, anche solo una sezione (`../../_config/stile.md#Tono`).
  Il livello si scrive come in ICM; se manca, è 3 per quello che sta in `references/`, `_config/`, `shared/`, e 4
  per il resto.
- **Tools**: alla fase si danno solo questi strumenti di Nova, non tutti (anche questo è contesto in meno).
  Senza la sezione, nessuno strumento.
- **Verify**: controlli prima di passare alla fase dopo (è la sezione che il paper propone come sviluppo futuro).
  Quelli semplici li fa il codice (`contiene: …`, `non contiene: …`); per gli altri giudica il modello.
- **Review**: `sì` = Nova si ferma e avvisa «la bozza è pronta, guardala»; `no` = prosegue da sola. Senza la
  sezione vale quello che osserva il paper: si corregge molto la prima e l'ultima fase e poco quelle in mezzo,
  quindi revisione alla prima e all'ultima.

**Conti fatti dal codice.** L'azione `tabella` lavora sulle tabelle scritte dalla fase prima (Markdown o CSV):
`action: tabella ../01_righe/output/righe.md: calcola totale riga = quantità * prezzo unitario; somma totale riga -> righe_totali.md`
oppure `somma importo per categoria`. Con `-> nome` il risultato va dritto in quel file della fase. Il modello
classifica e scrive; i numeri li somma il computer (la prima misura ha mostrato che un modello da 4 miliardi, anche con
la calcolatrice, sbaglia le somme di 20–30 importi).

Oltre a somme e colonne, la tabella sa **filtrare** (`filtra tipo = da fare`), **ordinare** (`ordina per scadenza`,
anche con date in italiano come «12 ottobre») e calcolare **valori con regole semplici**
(`valore sconto = se imponibile > 1000 allora imponibile * 10% altrimenti 0`): mettere in ordine le date e fare i conti
con le regole erano gli errori più frequenti del modello nella seconda misura.

**Un controllo che non passa non butta via il lavoro.** Se dopo un nuovo tentativo la fase non passa ancora i
controlli, il risultato resta e la fase si ferma «da guardare», con il problema scritto: l'utente corregge o dice
«vai avanti», come in una revisione.

**Una cosa alla volta.** Con la sezione `## For each` e una cartella, la fase lavora un file alla volta (una mail,
un documento) e mette i risultati uno sotto l'altro: il modello non vede mai tutto insieme.

**Controllo dei numeri.** Nella sezione Verify, `riporta i numeri di ../02_conti/output/righe_totali.md` controlla
che il risultato contenga tutti i numeri di quel file: un prezzo inventato o ricopiato male non passa.

**Fasi senza modello.** Il lavoro meccanico non passa dal modello (la regola 60/30/10 di ICM): una riga
`action: search_files bollette ottobre` nella sezione **Process** chiama lo strumento con quegli argomenti e scrive il
risultato, senza chiedere niente al modello. Raccogliere file, mandare la mail finale, salvare un PDF sono
azioni; scrivere, riassumere, decidere sono del modello.

## Come si esegue

Un esecutore nuovo (`procedure.py`), che usa i pezzi che SoIA ha già:

1. Per ogni fase in ordine: legge il contratto, prepara il contesto (identità della procedura, contratto, i file
   della sezione Inputs e niente altro), dà al modello solo gli strumenti della fase, salva il risultato in
   `output/`.
2. Esegue i controlli. Se falliscono, il modello riprova una volta con l'errore davanti; se falliscono ancora, la
   procedura si ferma lì e Nova dice cosa non torna.
3. Se la fase ha la revisione, si ferma e avvisa (centro notifiche). L'utente corregge il documento, se vuole, e
   dice «vai avanti».
4. **Ripresa**: lo stato è nei file. Dopo un riavvio, o se l'utente corregge una fase, si riparte dalla prima fase
   che non ha un risultato valido.
5. **Rifare solo quello che serve** (compilazione incrementale, sezione 6.1 del paper): se l'utente cambia un file
   di riferimento o un risultato, sono da rifare solo le fasi che lo leggono e quelle dopo.

Si appoggia a:

- **precedenza** (`precedenza.py`): una procedura è un lavoro di sottofondo, cede il passo all'utente;
- **registro delle azioni** (`azioni.py`): tutto quello che una procedura cambia fuori dalla sua cartella si annulla;
- **versioni dei documenti** (`versioni.py`): i risultati dei giri passati restano come versioni precedenti;
- **agenda**: «ogni mese il 1° alle 9» è un impegno ripetuto che avvia la procedura;
- **conferme**: le azioni che escono dal PC (mail, invii) chiedono conferma come sempre, a meno che l'utente
  dica per quella procedura «manda pure senza chiedere».

Modelli: il modello locale per le fasi che scrivono e decidono, il nucleo per i controlli che richiedono un
giudizio. Il cloud solo se l'utente l'ha acceso e solo per le procedure che lo permettono: i dati sono suoi.

## Come nasce una procedura

- **Parlando**: «ogni mese voglio il resoconto delle spese». Nova fa poche domande (da dove prendo le spese? come
  le dividi? lo vuoi vedere prima che lo mandi?), propone le fasi, l'utente approva e la cartella viene scritta.
  È il «costruttore» del paper: le procedure che crea sono tutte fatte allo stesso modo.
- **Da un modello**: SoIA ne porta qualcuna pronta (resoconto spese, riassunto della posta, preventivo).
- **Copiando**: «fai come il resoconto spese, ma per le spese della casa al mare». Nel paper è il modo in cui la
  gente le crea più spesso.
- **Usando** una cartella ICM fatta da altri (licenza MIT, formato aperto): è già nel formato giusto.

## La procedura migliora con l'uso

È l'idea che il paper descrive come futura (sezione 6.3, «correggi la fonte, non il risultato»), e SoIA può
farla subito:

- quando l'utente corregge un risultato, la differenza tra quello che Nova aveva scritto e quello che l'utente ha
  salvato si conserva in `.nova/`;
- se la stessa correzione torna tre giri di fila nella stessa fase («accorcia sempre l'introduzione», «cambia
  sempre "Gentile" in "Ciao"»), Nova propone di metterla nel contratto della fase o nei riferimenti: «Ogni volta accorci
  l'apertura: vuoi che lo scriva nelle istruzioni?»;
- se l'utente accetta, la modifica è una riga nel `CONTEXT.md` della fase, visibile e annullabile.

## Con Nova (fatto)

- **Crearle parlando**: «ogni mese voglio il resoconto delle spese» → Nova usa il modello pronto (spese, posta,
  preventivo) o le fasi dette dall'utente («prendi gli articoli; scegli i tre più importanti; scrivi la mail»), scrive
  la cartella in `~/Procedure/` e dice dove mettere i file (`input/`) e le regole (`_config/`).
- **Modelli pronti** (`procedure_crea.py`), quelli misurati: *Resoconto spese* (ogni mese il 1 alle 9; somme per
  categoria col codice), *Cose da fare dalla posta* (ogni lunedì alle 8; azione `posta 7` per le mail della settimana,
  filtro e ordine per scadenza col codice), *Preventivo* (a mano; tutti i conti col codice dalle regole).
- **Frasi riconosciute senza modello** (`tools/procedure.py`): «fai il resoconto spese», «a che punto è il resoconto?»,
  «vai avanti» (se c'è una sola procedura che aspetta), «rifai i conti del resoconto spese», «le mie procedure».
  «Avvia X» vale solo se X è davvero il nome di una procedura: «avvia Spotify» o «avvia la posta» restano alle app.
- **In sottofondo**: la procedura cede il passo all'utente e avvisa (notifica) quando è finita o quando una fase è da
  guardare.
- **Quando parte da sola**: «ogni mese il 1 alle 9», «ogni lunedì alle 8», «ogni giorno alle 18» (`.nova/quando.json`);
  si conta da quando la si imposta, e un solo Nova aperto alla volta le fa partire.

## Come si vede

- **App File**: una cartella di procedura si apre con una vista apposita: le fasi in fila con il loro stato
  (fatta, da guardare, da rifare, ferma per un controllo), i pulsanti *Avvia*, *Riprendi*, *Apri il risultato*,
  *Modifica le istruzioni*. Sotto, la cartella normale per chi vuole i file.
- **A voce**: «fai il resoconto spese», «a che punto è il resoconto?», «rifai solo i conti», «vai avanti»,
  «crea una procedura per…», «le mie procedure».
- **Impostazioni › Procedure**: elenco, prossima esecuzione, cloud sì/no per ognuna.

## Compatibilità con ICM

La cartella è nel formato ICM originale. Le sezioni in più (Tools, Verify, Review) ICM le ignora; una cartella ICM
senza di esse in SoIA funziona così: nessuno strumento, nessun controllo, revisione alla prima e all'ultima fase.

## Come si misura

Il paper non ha un confronto controllato (lo dicono gli autori) e ha provato una sola famiglia di modelli, Claude.
Noi possiamo farlo, ed è la cosa che decide se tenerlo:

- tre procedure di prova sui dati della persona inventata di `anteprima.py` (Giulia): resoconto spese dalle
  bollette, riassunto della posta, preventivo da una richiesta;
- ciascuna eseguita **tutta in un prompt** e **a fasi**, con il modello locale, più volte;
- si misurano: risultati giusti (controlli automatici: i numeri tornano, le voci ci sono), token nel contesto,
  tempo, quante volte serve riprovare.

## In che ordine

1. **Formato ed esecutore**: lettura della cartella e dei contratti, contesto per fase, strumenti per fase,
   azioni senza modello, controlli, revisione, ripresa. Con le prove.
2. **La misura** con le tre procedure di prova: tutto in un prompt contro a fasi, modello locale. Se a fasi non
   va meglio con i nostri modelli, ci si ferma qui e si decide insieme.
3. **Nova**: crearle parlando, avviarle, «a che punto è», «vai avanti»; avvio a orario; modelli pronti. *Fatto.*
4. **App File e Impostazioni**: la vista della procedura. *Fatto.*
5. **Correzioni che diventano istruzioni.** *Fatto:* ogni differenza tra il risultato com'era uscito (`.nova/original/`) e quello lasciato dall'utente si annota (`.nova/correzioni.jsonl`); dopo tre giri corretti la regola comune (la trova il codice se è sempre la stessa sostituzione, altrimenti il modello) si propone con una notifica, in Impostazioni › Procedure e a voce («sì, scrivila nelle istruzioni»).
6. **Il programmatore delle personalizzazioni a fasi.** *Fatto* (`StagedProgrammer` in `programmatore.py`), con
   controlli automatici al posto della revisione dell'utente:
   1. *piano* (modello, nessuno strumento): un JSON con pagina, file, cosa cercare, cosa cambiare, cosa deve
      risultare vero dopo; i file che non esistono si scartano;
   2. *codice* (lo fa il codice): grep dei termini del piano, le righe intorno a ogni risultato (al massimo ~14 000
      caratteri), prima i file del piano;
   3. *modifica* (modello): vede solo la richiesta, il piano e quei pezzi; può solo cercare, leggere e modificare;
   4. *verifica* (codice): `codice.check`, poi il controllo visivo della pagina con i controlli del piano; se non va,
      torna alla 3 con il problema scritto (al massimo tre giri);
   5. *riassunto* (modello, con un testo di riserva).
   Le fasi restano scritte in `lavoro/` accanto allo stato della copia personale. Si usa a fasi col modello del PC,
   il ciclo libero di prima col cloud; `AIOS_PROGRAMMATORE=fasi|libero` lo decide a mano.
   Misura (Release `programmatore-1`, qwen3.5:4b su CPU, 7 richieste × 3 giri, controllo visivo spento): lavori tutti
   giusti 15/21 a fasi contro 10/21 libero (13/21 contando giusto «colore», dove il libero ha cambiato anche il
   turchese di pannello.html: il controllo era troppo stretto); finiti 21/21 contro 16/21; token letti in media
   18 500 contro 52 000; tempo uguale (~3,5 minuti). A fasi sbaglia quando il piano sceglie l'elemento sbagliato
   («l'ora» → `#ora` della barra invece di `.orologio-ora` del widget; «testo grigio» → `--testo`).

## Deciso

- Nomi delle cartelle e dei file: quelli originali di ICM.
- Posizione: `~/Procedure/`.
- Revisione di regola: alla prima e all'ultima fase, come suggerisce il paper.
