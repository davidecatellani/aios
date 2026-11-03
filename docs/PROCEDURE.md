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

Le procedure stanno in `~/Procedure/`, una cartella per procedura. Nomi in italiano, perché la cartella la apre
l'utente; con l'importazione e l'esportazione si passa al formato ICM originale (tabella più sotto).

```
~/Procedure/Resoconto spese/
├── PROCEDURA.md              cos'è, a cosa serve, quando parte, le fasi        (livelli 0 e 1 di ICM)
├── riferimenti/              regole che valgono sempre: categorie, stile…     (livello 3, «la fabbrica»)
│   └── categorie.md
├── fasi/
│   ├── 01 Raccolta/
│   │   ├── ISTRUZIONI.md     il contratto della fase                           (livello 2)
│   │   └── risultato/        quello che la fase ha prodotto in questo giro     (livello 4, «il prodotto»)
│   ├── 02 Conti/
│   │   ├── ISTRUZIONI.md
│   │   └── risultato/
│   └── 03 Resoconto/
│       ├── ISTRUZIONI.md
│       └── risultato/
└── .nova/                    stato del giro, correzioni dell'utente, registro  (nascosta)
```

Il contratto di una fase (`ISTRUZIONI.md`) ha sezioni fisse:

```markdown
## Legge
- fasi/01 Raccolta/risultato/bollette.md
- riferimenti/categorie.md

## Fa
Somma le spese per categoria e confrontale con il mese prima.

## Scrive
- conti.md

## Strumenti
- calculate

## Controlla
- ogni categoria di categorie.md compare in conti.md
- il totale è la somma delle categorie

## Revisione
no
```

- **Legge**: solo questi file entrano nel contesto, anche solo una sezione (`riferimenti/stile.md#Tono`).
- **Strumenti**: alla fase si danno solo questi strumenti di Nova, non tutti (anche questo è contesto in meno).
- **Controlla**: controlli prima di passare alla fase dopo (è la sezione *Verify* che il paper propone come
  sviluppo futuro). Quando si può, li fa il codice (c'è il numero? c'è ogni categoria?); quando serve un
  giudizio, il nucleo, come nella verifica delle personalizzazioni.
- **Revisione**: `sì` = Nova si ferma e avvisa «la bozza è pronta, guardala»; `no` = prosegue da sola.
  Il paper osserva che si corregge molto la prima e l'ultima fase e poco quelle in mezzo: di regola sarà
  `sì` alla prima e all'ultima.

**Fasi senza modello.** Il lavoro meccanico non passa dal modello (la regola 60/30/10 di ICM): una riga
`azione: search_files bollette ottobre` nella sezione **Fa** chiama lo strumento con quegli argomenti e scrive il
risultato, senza chiedere niente al modello. Raccogliere file, mandare la mail finale, salvare un PDF sono
azioni; scrivere, riassumere, decidere sono del modello.

## Come si esegue

Un esecutore nuovo (`procedure.py`), che usa i pezzi che SoIA ha già:

1. Per ogni fase in ordine: legge il contratto, prepara il contesto (identità della procedura, contratto, i file
   della sezione Legge e niente altro), dà al modello solo gli strumenti della fase, salva il risultato in
   `risultato/`.
2. Esegue i controlli. Se falliscono, il modello riprova una volta con l'errore davanti; se falliscono ancora, la
   procedura si ferma lì e Nova dice cosa non torna.
3. Se la fase ha la revisione, si ferma e avvisa (centro notifiche). L'utente corregge il documento, se vuole, e
   dice «vai avanti».
4. **Ripresa**: lo stato è nei file. Dopo un riavvio, o se l'utente corregge una fase, si riparte dalla prima fase
   che non ha un risultato valido.
5. **Rifare solo quello che serve** (compilazione incrementale, sezione 6.1 del paper): se l'utente cambia un file
   in `riferimenti/` o un risultato, sono da rifare solo le fasi che lo leggono e quelle dopo.

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
- **Importando** una cartella ICM fatta da altri (licenza MIT, formato aperto).

## La procedura migliora con l'uso

È l'idea che il paper descrive come futura (sezione 6.3, «correggi la fonte, non il risultato»), e SoIA può
farla subito:

- quando l'utente corregge un risultato, la differenza tra quello che Nova aveva scritto e quello che l'utente ha
  salvato si conserva in `.nova/correzioni/`;
- se la stessa correzione torna tre giri di fila nella stessa fase («accorcia sempre l'introduzione», «cambia
  sempre "Gentile" in "Ciao"»), Nova propone di metterla nelle istruzioni o nei riferimenti: «Ogni volta accorci
  l'apertura: vuoi che lo scriva nelle istruzioni?»;
- se l'utente accetta, la modifica è una riga in `ISTRUZIONI.md`, visibile e annullabile.

## Come si vede

- **App File**: una cartella di procedura si apre con una vista apposita: le fasi in fila con il loro stato
  (fatta, da guardare, da rifare, ferma per un controllo), i pulsanti *Avvia*, *Riprendi*, *Apri il risultato*,
  *Modifica le istruzioni*. Sotto, la cartella normale per chi vuole i file.
- **A voce**: «fai il resoconto spese», «a che punto è il resoconto?», «rifai solo i conti», «vai avanti»,
  «crea una procedura per…», «le mie procedure».
- **Impostazioni › Procedure**: elenco, prossima esecuzione, cloud sì/no per ognuna.

## Formato ICM e importazione

| ICM | SoIA |
|---|---|
| `CLAUDE.md` + `CONTEXT.md` (radice) | `PROCEDURA.md` |
| `stages/01_research/` | `fasi/01 Raccolta/` |
| `CONTEXT.md` della fase (Inputs, Process, Outputs) | `ISTRUZIONI.md` (Legge, Fa, Scrive, più Strumenti, Controlla, Revisione) |
| `references/`, `_config/`, `shared/` | `riferimenti/` |
| `output/` | `risultato/` |
| `setup/questionnaire.md` | le domande del costruttore |

L'importazione traduce le sezioni (Inputs → Legge…); le sezioni che ICM non ha restano vuote: nessuno
strumento, nessun controllo, revisione a ogni fase, che è il comportamento di ICM.

## Come si misura

Il paper non ha un confronto controllato (lo dicono gli autori) e ha provato una sola famiglia di modelli, Claude.
Noi possiamo farlo, ed è la cosa che decide se tenerlo:

- tre procedure di prova sui dati della persona inventata di `anteprima.py` (Giulia): resoconto spese dalle
  bollette, riassunto della posta, preventivo da una richiesta;
- ciascuna eseguita **tutta in un prompt** e **a fasi**, con il modello locale, più volte;
- si misurano: risultati giusti (controlli automatici: i numeri tornano, le voci ci sono), token nel contesto,
  tempo, quante volte serve riprovare.

## In che ordine

1. **Formato ed esecutore**: lettura di `PROCEDURA.md` e `ISTRUZIONI.md`, contesto per fase, strumenti per fase,
   azioni senza modello, controlli, revisione, ripresa. Con le prove.
2. **La misura** con le tre procedure di prova: tutto in un prompt contro a fasi, modello locale. Se a fasi non
   va meglio con i nostri modelli, ci si ferma qui e si decide insieme.
3. **Nova**: crearle parlando, avviarle, «a che punto è», «vai avanti»; avvio dall'agenda; modelli pronti.
4. **App File e Impostazioni**: la vista della procedura.
5. **Correzioni che diventano istruzioni.**
6. **Importazione ed esportazione ICM.**
7. Dopo: il programmatore delle personalizzazioni a fasi con lo stesso esecutore (con controlli automatici al
   posto della revisione dell'utente).

## Da decidere

- **Nomi in italiano o quelli di ICM** nella cartella dell'utente: qui sono in italiano (la cartella la apre una
  persona qualunque), con la traduzione all'importazione e all'esportazione.
- **Dove stanno**: `~/Procedure/` visibile, oppure dentro `~/Documenti/Procedure/`.
- **Revisione di regola**: alla prima e all'ultima fase (come suggerisce il paper), oppure a tutte finché l'utente
  non si fida.
