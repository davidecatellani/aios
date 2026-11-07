"""La misura delle procedure: lo stesso lavoro tutto in un prompt oppure a fasi (ICM, aios_copilot/procedure.py),
con lo stesso modello locale, gli stessi dati e la stessa calcolatrice.

    python addestramento/procedure_prova.py --modello qwen3.5:4b --quante 4 [--compiti spese,posta,preventivo]

Tre compiti, con dati inventati diversi a ogni giro e la risposta giusta nota, così il controllo è automatico:

- spese: dall'estratto conto del mese, il resoconto per la famiglia (totale per categoria, totale del mese,
  la categoria più costosa);
- posta: da venti mail, le cose da fare con chi e entro quando, in ordine di scadenza, senza la pubblicità;
- preventivo: da una richiesta scritta a parole e dal listino, il preventivo con righe, sconto, spedizione, IVA.

Il modo «unico» è una procedura con una fase sola che legge tutto (le istruzioni di tutte le fasi, le regole, i
dati) e scrive il risultato finale: è il prompt unico del paper. Il modo «fasi» è la procedura vera, con i conti
fatti dal codice (azione «tabella») e i controlli (Verify) di ogni fase; il modo unico ha i controlli che si possono
fare sul solo risultato finale. Senza revisione in entrambi; se una fase non passa i controlli si ferma «da
guardare»: qui si va avanti con quello che c'è e la fermata si conta a parte («fermate per controllo»).

- posta_lunga: la posta di un mese, 60 mail lunghe in file separati (molto contesto): a fasi la prima fase legge
  una mail alla volta («For each»), nel modo unico il modello le legge tutte insieme.
Esce un JSON con i dettagli e una tabella in Markdown.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aios_copilot import procedure  # noqa: E402
from aios_copilot.tools.calcolo import make_tools as calc_tools  # noqa: E402


# --- i compiti ----------------------------------------------------------------------------------------------
@dataclass
class Task:
    name: str
    identity: str
    files: dict[str, str]                 # percorso nella procedura → testo (input/ e _config/)
    stages: list[tuple[str, str]]         # (cartella, CONTEXT.md)
    final: str                            # il file finale dell'ultima fase
    checks: list[tuple[str, Callable[[str], bool]]] = field(default_factory=list)
    answer: str = ""                      # un risultato giusto (per controllare i controlli)


def euro(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


NUM = re.compile(r"(?<![\d,.])(\d{1,3}(?:\.\d{3})+,\d{1,2}|\d+,\d{1,2}|\d{1,3}(?:,\d{3})+\.\d{1,2}|\d+\.\d{1,2}|\d+)(?![\d,])")


def numbers(text: str) -> list[float]:
    out = []
    for raw in NUM.findall(text):
        s = raw
        if "," in s and ("." not in s or s.rfind(",") > s.rfind(".")):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return out


def has_number(value: float) -> Callable[[str], bool]:
    return lambda text: any(abs(n - value) < 0.011 for n in numbers(text))


def has_word(word: str) -> Callable[[str], bool]:
    return lambda text: word.lower() in text.lower()


def lacks_word(word: str) -> Callable[[str], bool]:
    return lambda text: word.lower() not in text.lower()


CATEGORIES = {
    "Casa": [("Enel Energia bolletta luce", 50, 120), ("Italgas bolletta gas", 25, 110), ("Hera acqua", 15, 45),
             ("Fastweb internet", 27.9, 29.9), ("Condominio rata", 80, 160)],
    "Spesa": [("Esselunga", 25, 140), ("Coop", 15, 90), ("Conad", 10, 70), ("Panificio Rossi", 3, 12)],
    "Trasporti": [("Eni benzina", 40, 75), ("Trenitalia biglietto", 9, 45), ("Telepass", 12, 40)],
    "Salute": [("Farmacia Comunale", 6, 40), ("Studio dentistico Sorriso", 60, 150)],
    "Svago": [("Cinema Odeon", 8, 24), ("Netflix", 13.99, 13.99), ("Pizzeria Da Gino", 25, 70), ("Libreria Feltrinelli", 10, 35)],
}


def task_spese(rng: random.Random) -> Task:
    rows, totals = [], {c: 0.0 for c in CATEGORIES}
    for cat, shops in CATEGORIES.items():
        for shop, lo, hi in rng.sample(shops, k=min(len(shops), rng.randint(2, len(shops)))):
            for _ in range(rng.choice([1, 1, 2, 3]) if cat in ("Spesa", "Svago", "Trasporti") else 1):
                amount = round(rng.uniform(lo, hi), 2)
                rows.append((rng.randint(1, 30), shop, amount))
                totals[cat] = round(totals[cat] + amount, 2)
    rng.shuffle(rows)
    rows.sort(key=lambda r: r[0])
    month = rng.choice(["settembre", "ottobre", "novembre"])
    previous = {c: round(t * rng.uniform(0.7, 1.3), 2) for c, t in totals.items()}
    total = round(sum(totals.values()), 2)
    top = max(totals, key=totals.get)
    statement = "data;descrizione;importo\n" + "\n".join(f"{d:02d}/{month};{shop};-{euro(a)}" for d, shop, a in rows)
    rules = "# Categorie\n\n" + "\n".join(f"- **{c}**: " + ", ".join(s for s, _, _ in shops) for c, shops in CATEGORIES.items())
    prev = "# Mese precedente\n\n" + "\n".join(f"- {c}: {euro(v)} €" for c, v in previous.items())
    t = Task("spese", f"# Resoconto spese\nOgni mese il resoconto delle spese di {month} per la famiglia, dall'estratto conto.\n",
             {"input/estratto_conto.csv": statement, "_config/categorie.md": rules, "input/mese_precedente.md": prev,
              "_config/stile.md": "# Stile\nFrasi brevi, tono familiare, importi in euro con la virgola (es. 12,50 €). Al massimo 200 parole."},
             [("01_classifica", """## Inputs
- Layer 4 (working): ../../input/estratto_conto.csv
- Layer 3 (reference): ../../_config/categorie.md

## Process
Assegna ogni movimento dell'estratto conto a una categoria di categorie.md. Scrivi solo una tabella in Markdown con
le colonne | data | descrizione | importo | categoria |, una riga per movimento, senza saltarne nessuno; l'importo
copialo com'è.

## Outputs
- movimenti.md -> output/

## Verify
- riporta i numeri di ../../input/estratto_conto.csv
"""), ("02_conti", """## Inputs
- Layer 4 (working): ../../input/mese_precedente.md

## Process
action: tabella ../01_classifica/output/movimenti.md: somma importo per categoria -> totali.md
Confronta i totali di questo mese con quelli del mese precedente: per ogni
categoria scrivi il totale di questo mese, quello del mese precedente e se è aumentata o diminuita.

## Outputs
- totali.md -> output/
- confronto.md -> output/
"""), ("03_resoconto", """## Inputs
- Layer 4 (working): ../02_conti/output/totali.md
- Layer 4 (working): ../02_conti/output/confronto.md
- Layer 3 (reference): ../../_config/stile.md

## Process
Scrivi il resoconto del mese per la famiglia: il totale di ogni categoria, il totale del mese, la categoria in cui
si è speso di più e un confronto breve con il mese precedente. Usa i numeri di totali.md così come sono.

## Outputs
- resoconto.md -> output/

## Verify
- riporta i numeri di ../02_conti/output/totali.md
- contiene: Totale
""")], "resoconto.md")
    t.checks = [(f"totale {c}", has_number(v)) for c, v in totals.items()]
    t.checks += [("totale del mese", has_number(total)), (f"categoria più costosa ({top})", has_word(top))]
    t.answer = ("\n".join(f"- {c}: {euro(v)} €" for c, v in totals.items())
                + f"\nTotale del mese: {euro(total)} €. Si è speso di più in {top}.\n")
    return t


PEOPLE = [("Laura", "Verdi", "acme-srl.com"), ("Paolo", "Bianchi", "studiobianchi.it"), ("Anna", "Neri", "comune.bologna.it"),
          ("Sergio", "Galli", "scuola-pascoli.it"), ("Chiara", "Fontana", "condominio-aurora.it"), ("Davide", "Moretti", "gmail.com"),
          ("Elena", "Ricci", "asl.bologna.it"), ("Marco", "Rossi", "gmail.com")]
TODOS = [("mandarle il report trimestrale firmato", "Report trimestrale"), ("portare la dichiarazione dei redditi e le ricevute mediche", "Documenti per il 730"),
         ("rinnovare la carta d'identità prenotando sul sito", "Carta d'identità in scadenza"), ("firmare l'autorizzazione per la gita", "Gita scolastica"),
         ("pagare la quota straordinaria per il tetto", "Lavori al tetto"), ("confermare la presenza alla cena", "Cena di classe"),
         ("prenotare la visita di controllo", "Visita di controllo"), ("restituire il trapano prestato", "Il mio trapano")]
INFO = [("Foto delle vacanze", "Ti mando le foto di Rimini, guarda che belle!"), ("Verbale assemblea", "In allegato il verbale dell'assemblea, solo per conoscenza."),
        ("Cambio orario ufficio", "Da lunedì l'ufficio apre alle 8:30. Nessuna azione richiesta."), ("Grazie!", "Grazie ancora per l'aiuto di ieri."),
        ("Newsletter del quartiere", "Questo mese in quartiere: mercatino domenica in piazza.")]
PROMO = [("Zalando", "Saldi d'autunno fino al -40%, approfitta entro domenica!"), ("Amazon", "Offerte del giorno: sconti su elettronica e casa."),
         ("Booking", "Le tue prossime vacanze ti aspettano: prenota entro venerdì e risparmia."), ("Unieuro", "Volantino della settimana: TV a prezzi bassi."),
         ("Decathlon", "Nuova collezione inverno: scopri le offerte."), ("Spotify", "Prova Premium gratis per 3 mesi, offerta valida fino al 30.")]
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def task_posta(rng: random.Random) -> Task:
    month = rng.randrange(12)
    people = rng.sample(PEOPLE, 8)
    todos = rng.sample(TODOS, 5)
    days = sorted(rng.sample(range(3, 29), 5))
    rng.shuffle(days)
    actions = []
    mails = []
    for (first, last, dom), (what, subject), day in zip(people[:5], todos, days):
        when = f"{day} {MONTHS[month]}"
        actions.append((day, last))
        mails.append((f"{first} {last} <{first.lower()}.{last.lower()}@{dom}>", subject,
                      f"Ciao, ti chiedo di {what} entro il {when}. Grazie, {first}"))
    for (first, last, dom), (subject, text) in zip(people[5:], rng.sample(INFO, 3)):
        mails.append((f"{first} {last} <{first.lower()}@{dom}>", subject, text))
    promos = rng.sample(PROMO, 6)
    for brand, text in promos:
        mails.append((f"{brand} <offerte@{brand.lower()}.it>", f"{brand}: offerte per te", text))
    for brand, text in rng.sample(PROMO, 6):  # qualche promozione ripetuta, come nella posta vera
        if len(mails) < 20:
            mails.append((f"{brand} <newsletter@{brand.lower()}.it>", f"Ultimi giorni da {brand}", text))
    rng.shuffle(mails)
    box = "\n\n".join(f"### Mail {i}\nDa: {who}\nOggetto: {subj}\n\n{text}" for i, (who, subj, text) in enumerate(mails, 1))
    t = Task("posta", "# Cose da fare dalla posta\nOgni settimana, dalle mail ricevute, l'elenco delle cose da fare.\n",
             {"input/posta.md": box,
              "_config/regole.md": "# Regole\n- Una cosa da fare è una mail di una persona che chiede di fare qualcosa entro una data.\n"
                                   "- Pubblicità, offerte e newsletter non sono cose da fare: non nominarle.\n"
                                   "- Per ogni cosa da fare: chi la chiede (nome e cognome), cosa, entro quando (giorno e mese).\n"
                                   "- In ordine di scadenza: prima la più vicina."},
             [("01_classifica", """## Inputs
- Layer 4 (working): ../../input/posta.md
- Layer 3 (reference): ../../_config/regole.md

## Process
Scrivi solo una tabella in Markdown con le colonne | mittente | tipo | cosa | scadenza |, una riga per mail:
mittente (nome e cognome), tipo (da fare, informazione o pubblicità) e, se è da fare, cosa chiede e la scadenza
(giorno e mese, per esempio «12 ottobre»).

## Outputs
- classificate.md -> output/
"""), ("02_da_fare", """## Process
action: tabella ../01_classifica/output/classificate.md: filtra tipo = da fare; ordina per scadenza -> da_fare.md

## Outputs
- da_fare.md -> output/
"""), ("03_messaggio", """## Inputs
- Layer 4 (working): ../02_da_fare/output/da_fare.md
- Layer 3 (reference): ../../_config/regole.md

## Process
Scrivi il messaggio per l'utente: l'elenco numerato delle cose da fare, in ordine di scadenza, con chi la chiede
(nome e cognome), cosa ed entro quando. Niente altro.

## Outputs
- cose_da_fare.md -> output/

## Verify
- riporta i numeri di ../02_da_fare/output/da_fare.md
""")], "cose_da_fare.md")
    for day, last in actions:
        t.checks.append((f"{last} con la scadenza {day} {MONTHS[month]}",
                         lambda text, last=last, day=day: any(last in line and re.search(rf"\b{day}\b", line) for line in text.splitlines())))
    order = [last for _, last in sorted(actions)]
    t.checks.append(("in ordine di scadenza", lambda text, order=order: _in_order(text, order)))
    for brand, _ in promos[:3]:
        t.checks.append((f"senza la pubblicità di {brand}", lacks_word(brand)))
    t.answer = "\n".join(f"{n}. {last}: entro il {day} {MONTHS[month]}" for n, (day, last) in enumerate(sorted(actions), 1)) + "\n"
    return t


FILLER = ["Scopri le novità della settimana selezionate per te dal nostro team.", "Approfitta delle promozioni riservate agli iscritti.",
          "Spedizione gratuita sopra i 49 euro e reso facile entro 30 giorni.", "Ti ricordiamo che puoi gestire le preferenze dal tuo profilo.",
          "Le offerte sono valide fino a esaurimento scorte.", "Ecco i prodotti più amati dai nostri clienti questo mese.",
          "Non perdere il nostro evento speciale con sconti fino al 50%.", "Hai punti fedeltà in scadenza: usali entro fine mese.",
          "Leggi gli articoli più letti della settimana sul nostro blog.", "Consigli utili per organizzare al meglio la tua casa.",
          "Il nostro servizio clienti è disponibile tutti i giorni dalle 9 alle 18.", "Ricevi questa mail perché sei iscritto alla newsletter.",
          "Per non ricevere più queste comunicazioni clicca qui.", "Una selezione di idee regalo per ogni occasione e budget.",
          "Prenota entro venerdì per bloccare il prezzo migliore.", "Le novità in arrivo nelle prossime settimane ti sorprenderanno."]
BRANDS = ["Zalando", "Amazon", "Booking", "Unieuro", "Decathlon", "Spotify", "Ikea", "Feltrinelli", "Trenitalia", "Esselunga", "Mediaworld", "Netflix"]


def task_posta_lunga(rng: random.Random) -> Task:
    """La posta di un mese: 60 mail lunghe, una per file (newsletter, offerte, risposte con il messaggio citato)."""
    month = rng.randrange(1, 12)
    people = rng.sample(PEOPLE, 8)
    todos = rng.sample(TODOS, 6)
    people += [("Giorgio", "Conti", "studio-conti.it"), ("Marta", "Leone", "gmail.com")]
    days = rng.sample(range(3, 29), 6)
    actions, mails = [], []
    for (first, last, dom), (what, subject), day in zip(people[:6], todos, days):
        old = rng.randint(1, 28)
        quoted = (f"\n\nIl {old} {MONTHS[month - 1]} {first} ha scritto:\n> Ti scrivo per l'incontro del {old} {MONTHS[month - 1]},"
                  " che poi abbiamo rimandato. " + " ".join(rng.sample(FILLER[9:], 2)))
        actions.append((day, last))
        mails.append((f"{first} {last} <{first.lower()}.{last.lower()}@{dom}>", f"Re: {subject}",
                      f"Ciao, come ti dicevo ti chiedo di {what} entro il {day} {MONTHS[month]}. Fammi sapere. Grazie, {first}" + quoted))
    for i in range(10):
        first, last, dom = people[6 + i % 4]
        subject, text = rng.choice(INFO)
        mails.append((f"{first} {last} <{first.lower()}@{dom}>", subject, text + " " + " ".join(rng.sample(FILLER[9:], 3))))
    promos = []
    while len(mails) < 60:
        brand = rng.choice(BRANDS)
        promos.append(brand)
        body = " ".join(rng.choice(FILLER) for _ in range(rng.randint(10, 16)))
        mails.append((f"{brand} <newsletter@{brand.lower()}.it>", f"{brand}: le offerte di {MONTHS[month]}",
                       f"Offerta valida fino al {rng.randint(1, 28)} {MONTHS[month]}. " + body))
    rng.shuffle(mails)
    files = {f"input/posta/{i:02d}.md": f"Da: {who}\nOggetto: {subj}\n\n{text}" for i, (who, subj, text) in enumerate(mails, 1)}
    files["_config/regole.md"] = ("# Regole\n- Una cosa da fare è una mail di una persona che chiede di fare qualcosa entro una data.\n"
                                  "- Pubblicità, offerte e newsletter non sono cose da fare: non nominarle.\n"
                                  "- Il testo citato (righe che cominciano con «>») è un messaggio vecchio: non conta.\n"
                                  "- Per ogni cosa da fare: chi la chiede (nome e cognome), cosa, entro quando (giorno e mese).\n"
                                  "- In ordine di scadenza: prima la più vicina.")
    t = Task("posta_lunga", "# Cose da fare dalla posta del mese\nOgni mese, dalle mail ricevute, l'elenco delle cose da fare.\n", files,
             [("01_classifica", """## For each
../../input/posta/

## Inputs
- Layer 3 (reference): ../../_config/regole.md

## Process
Leggi la mail e rispondi solo con una tabella in Markdown di una riga, con le colonne | mittente | tipo | cosa | scadenza |:
mittente (nome e cognome), tipo (da fare, informazione o pubblicità) e, se è da fare, cosa chiede e la scadenza
(giorno e mese, per esempio «12 ottobre»).

## Outputs
- classificate.md -> output/
"""), ("02_da_fare", """## Process
action: tabella ../01_classifica/output/classificate.md: filtra tipo = da fare; ordina per scadenza -> da_fare.md

## Outputs
- da_fare.md -> output/
"""), ("03_messaggio", """## Inputs
- Layer 4 (working): ../02_da_fare/output/da_fare.md
- Layer 3 (reference): ../../_config/regole.md

## Process
Scrivi il messaggio per l'utente: l'elenco numerato delle cose da fare, in ordine di scadenza, con chi la chiede
(nome e cognome), cosa ed entro quando. Niente altro.

## Outputs
- cose_da_fare.md -> output/

## Verify
- riporta i numeri di ../02_da_fare/output/da_fare.md
""")], "cose_da_fare.md")
    for day, last in actions:
        t.checks.append((f"{last} con la scadenza {day} {MONTHS[month]}",
                         lambda text, last=last, day=day: any(last in line and re.search(rf"\b{day}\b", line) for line in text.splitlines())))
    order = [last for _, last in sorted(actions)]
    t.checks.append(("in ordine di scadenza", lambda text, order=order: _in_order(text, order)))
    for brand in list(dict.fromkeys(promos))[:3]:
        t.checks.append((f"senza la pubblicità di {brand}", lacks_word(brand)))
    t.answer = "\n".join(f"{n}. {last}: entro il {day} {MONTHS[month]}" for n, (day, last) in enumerate(sorted(actions), 1)) + "\n"
    return t


def _in_order(text: str, names: list[str]) -> bool:
    pos = [text.find(n) for n in names]
    return all(p >= 0 for p in pos) and pos == sorted(pos)


CATALOG = [("SED-01", "Sedia da ufficio ergonomica", 129.0), ("SED-02", "Sedia visitatore", 49.5), ("SCR-01", "Scrivania 160x80", 289.0),
           ("SCR-02", "Scrivania regolabile in altezza", 549.0), ("ARM-01", "Armadio a due ante", 319.0), ("CAS-01", "Cassettiera tre cassetti", 119.0),
           ("LAM-01", "Lampada da scrivania LED", 34.9), ("LIB-01", "Libreria cinque ripiani", 159.0), ("TAV-01", "Tavolo riunioni 8 posti", 689.0),
           ("PAR-01", "Pannello fonoassorbente", 64.0), ("APP-01", "Appendiabiti", 39.0), ("POG-01", "Poggiapiedi", 24.5),
           ("MON-01", "Braccio porta monitor", 59.9), ("CES-01", "Cestino gettacarte", 12.5), ("LAV-01", "Lavagna magnetica 120x90", 98.0)]
WORDS_FOR = {"SED-01": "sedie da ufficio di quelle ergonomiche", "SED-02": "sedie per gli ospiti", "SCR-01": "scrivanie normali da 160",
             "SCR-02": "scrivanie che si alzano e abbassano", "ARM-01": "armadi a due ante", "CAS-01": "cassettiere", "LAM-01": "lampade da tavolo",
             "LIB-01": "librerie", "TAV-01": "tavolo grande per le riunioni", "PAR-01": "pannelli contro il rumore", "APP-01": "appendiabiti",
             "POG-01": "poggiapiedi", "MON-01": "bracci per i monitor", "CES-01": "cestini", "LAV-01": "lavagne magnetiche"}


def task_preventivo(rng: random.Random) -> Task:
    items = rng.sample(CATALOG, rng.randint(3, 5))
    big = rng.random() < 0.6
    lines = []
    for code, desc, price in items:
        qty = rng.randint(2, 8) if big else rng.randint(1, 2)
        lines.append((code, desc, price, qty, round(price * qty, 2)))
    taxable = round(sum(l[4] for l in lines), 2)
    discount = round(taxable * 0.10, 2) if taxable > 1000 else 0.0
    shipping = 0.0 if taxable >= 300 else 25.0
    base = round(taxable - discount + shipping, 2)
    vat = round(base * 0.22, 2)
    total = round(base + vat, 2)
    client = rng.choice(["Studio Legale Martini", "Agenzia Viaggi Sole", "Scuola di Danza Aurora", "Officina Bertoni"])
    wish = ", ".join(f"{q} {WORDS_FOR[c]}" if q > 1 else f"un{'a' if WORDS_FOR[c].endswith('e') else ''} {WORDS_FOR[c]}"
                     for c, _, _, q, _ in lines)
    request = (f"Buongiorno, sono di {client}. Stiamo arredando il nuovo ufficio e ci servirebbero: {wish}. "
               "Ci può mandare un preventivo? Grazie.")
    listino = "# Listino\n\n| codice | articolo | prezzo unitario |\n|---|---|---|\n" + "\n".join(f"| {c} | {d} | {euro(p)} € |" for c, d, p in CATALOG)
    rules = ("# Regole del preventivo\n- Imponibile = somma delle righe (quantità × prezzo unitario).\n"
             "- Sconto del 10% sull'imponibile se l'imponibile supera 1.000 €.\n"
             "- Spedizione 25 € se l'imponibile è sotto 300 €, gratis altrimenti.\n"
             "- IVA 22% su (imponibile − sconto + spedizione).\n- Totale = imponibile − sconto + spedizione + IVA.\n"
             "- Importi con due decimali e la virgola (es. 1.234,50 €).")
    t = Task("preventivo", "# Preventivi\nPer ogni richiesta di un cliente, il preventivo dal listino.\n",
             {"input/richiesta.md": request, "_config/listino.md": listino, "_config/regole.md": rules},
             [("01_righe", """## Inputs
- Layer 4 (working): ../../input/richiesta.md
- Layer 3 (reference): ../../_config/listino.md

## Process
Trova nel listino gli articoli che il cliente chiede e quanti ne vuole. Scrivi solo una tabella in Markdown con le
colonne | codice | articolo | quantità | prezzo unitario |, una riga per articolo, con il prezzo del listino.

## Outputs
- righe.md -> output/

## Verify
- ogni articolo chiesto dal cliente ha una riga, con codice e prezzo presi dal listino
"""), ("02_conti", """## Process
action: tabella ../01_righe/output/righe.md: calcola totale riga = quantità * prezzo unitario; valore imponibile = somma(totale riga); valore sconto = se imponibile > 1000 allora imponibile * 10% altrimenti 0; valore spedizione = se imponibile < 300 allora 25 altrimenti 0; valore base_iva = imponibile - sconto + spedizione; valore IVA = base_iva * 22%; valore totale = base_iva + IVA -> conti.md

## Outputs
- conti.md -> output/
"""), ("03_preventivo", """## Inputs
- Layer 4 (working): ../02_conti/output/conti.md
- Layer 4 (working): ../../input/richiesta.md
- Layer 3 (reference): ../../_config/regole.md

## Process
Scrivi il preventivo per il cliente: intestazione con il nome del cliente, le righe con quantità, prezzo
unitario e totale di riga, poi imponibile, sconto, spedizione, IVA e totale. Usa i numeri così come sono, senza
rifare i conti. Una riga di saluti.

## Outputs
- preventivo.md -> output/

## Verify
- riporta i numeri di ../02_conti/output/conti.md
- contiene: Totale
""")], "preventivo.md")
    t.checks = [(f"riga {c} ({q} × {euro(p)})", has_number(lt)) for c, _, p, q, lt in lines]
    t.checks += [("imponibile", has_number(taxable)), ("IVA", has_number(vat)), ("totale", has_number(total)),
                 ("nome del cliente", has_word(client))]
    if discount:
        t.checks.append(("sconto", has_number(discount)))
    t.answer = (f"Preventivo per {client}\n" + "\n".join(f"- {c} {d}: {q} × {euro(p)} € = {euro(lt)} €" for c, d, p, q, lt in lines)
                + f"\nImponibile {euro(taxable)} €, sconto {euro(discount)} €, spedizione {euro(shipping)} €, "
                  f"IVA {euro(vat)} €, totale {euro(total)} €\n")
    return t


TASKS = {"spese": task_spese, "posta": task_posta, "preventivo": task_preventivo, "posta_lunga": task_posta_lunga}


# --- le due procedure -----------------------------------------------------------------------------------------
def write_workspace(task: Task, folder: Path, mode: str) -> procedure.Workspace:
    ws = folder / f"{task.name}-{mode}"
    for rel, text in task.files.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text + "\n")
    (ws / "CLAUDE.md").write_text(task.identity)
    if mode == "fasi":
        (ws / "CONTEXT.md").write_text("Fasi: " + " → ".join(k for k, _ in task.stages) + "\n")
        for key, contract in task.stages:
            (ws / "stages" / key).mkdir(parents=True)
            (ws / "stages" / key / "CONTEXT.md").write_text(contract + "\n## Review\nno\n")
    else:
        # una fase sola che legge tutto e fa tutto: il prompt unico
        inputs, steps, tools = [], [], set()
        for key, contract in task.stages:
            c = procedure.parse_contract(contract, ws / "stages" / key, ws)
            for inp in c.inputs:
                rel = "../../" + str(inp.path.relative_to(ws))
                if inp.path.is_relative_to(ws / "input") or inp.path.is_relative_to(ws / "_config"):
                    line = f"- Layer {inp.layer}: {rel}"
                    if line not in inputs:
                        inputs.append(line)
            # i conti che a fasi fa il codice, qui li deve fare il modello (con la calcolatrice)
            sums = [a.args.partition(":")[2].strip() for a in c.actions if a.tool.lower() in procedure.BUILTIN_ACTIONS]
            todo = "".join(f"Fai tu questi conti e operazioni sulla tabella (con calculate per i conti): {s}. " for s in sums)
            each = f"Per ogni file di {c.each.relative_to(ws)}/ (uno alla volta): " if c.each else ""
            steps.append(f"Passo {len(steps) + 1}: {each}{todo}{c.process}")
            if sums:
                tools.add("calculate")
            tools.update(c.tools)
        final = procedure.parse_contract(task.stages[-1][1], ws / "stages" / task.stages[-1][0], ws)
        verify = [r for r in final.verify if "../" not in r]  # i controlli sui file intermedi qui non hanno senso
        contract = (f"## Inputs\n" + "\n".join(inputs) + "\n\n## Process\nFai tutto il lavoro in un colpo solo, passo dopo passo, "
                    "e scrivi solo il risultato dell'ultimo passo.\n\n" + "\n\n".join(steps) + f"\n\n## Outputs\n- {task.final} -> output/\n"
                    + ("\n## Tools\n" + "\n".join(f"- {t}" for t in sorted(tools)) + "\n" if tools else "")
                    + ("\n## Verify\n" + "\n".join(f"- {r}" for r in verify) + "\n" if verify else "") + "\n## Review\nno\n")
        (ws / "stages" / "01_tutto").mkdir(parents=True)
        (ws / "stages" / "01_tutto" / "CONTEXT.md").write_text(contract)
    return procedure.Workspace(ws)


class Measured:
    """Il modello con Ollama, contando token e tempo di ogni richiesta (come Nova: niente ragionamento)."""

    def __init__(self, model: str, url: str, num_ctx: int, think: bool):
        self.model, self.url, self.num_ctx, self.think = model, url.rstrip("/"), num_ctx, think
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {"model": self.model, "messages": messages, "tools": tools, "stream": False,
                   "options": {"num_ctx": self.num_ctx, "temperature": 0.2}, "keep_alive": "60m"}
        if any(k in self.model.lower() for k in ("qwen3", "gemma4")):  # anche hf.co/…/Qwen_Qwen3.5-4B-GGUF
            payload["think"] = self.think
        start = time.monotonic()
        req = urllib.request.Request(f"{self.url}/api/chat", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1800) as resp:
            data = json.loads(resp.read())
        self.calls.append({"prompt": data.get("prompt_eval_count", 0), "risposta": data.get("eval_count", 0),
                           "secondi": round(time.monotonic() - start, 1)})
        return data["message"]


def score(task: Task, text: str) -> list[tuple[str, bool]]:
    return [(name, bool(check(text))) for name, check in task.checks]


def run_one(task: Task, mode: str, folder: Path, model: Measured) -> dict[str, Any]:
    ws = write_workspace(task, folder, mode)
    model.calls = []
    tools = {t.name: t for t in calc_tools()}
    start = time.monotonic()
    error = ""
    stops = []
    try:
        runner = procedure.Runner(ws, model, tools, wait=lambda: True)
        report = runner.run()
        while report.status == "da guardare" and len(stops) < 10:
            stops.append(report.message[:200])  # nella vita vera decide l'utente; qui si va avanti con quello che c'è
            report = runner.proceed()
        if report.status != "finita":
            error = report.message
    except Exception as exc:  # un giro rotto conta come sbagliato, non ferma la misura
        error = f"{type(exc).__name__}: {exc}"
    final = ws.stages()[-1].output_dir / task.final
    text = final.read_text() if final.is_file() else ""
    checks = score(task, text)
    return {"modo": mode, "controlli": checks, "giusti": sum(ok for _, ok in checks), "totale": len(checks),
            "tutto_giusto": all(ok for _, ok in checks) and bool(checks), "errore": error, "fermate": stops,
            "richieste": len(model.calls), "contesto_max": max((c["prompt"] for c in model.calls), default=0),
            "token_letti": sum(c["prompt"] for c in model.calls), "token_scritti": sum(c["risposta"] for c in model.calls),
            "secondi": round(time.monotonic() - start, 1), "risultato": text[:3000]}


def table(results: list[dict[str, Any]]) -> str:
    lines = ["| compito | modo | controlli giusti | lavori tutti giusti | fermate per controllo | contesto massimo (token) | token letti | secondi |",
             "|---|---|---|---|---|---|---|---|"]
    for name in dict.fromkeys(r["compito"] for r in results):
        for mode in ("unico", "fasi"):
            rs = [r for r in results if r["compito"] == name and r["modo"] == mode]
            if not rs:
                continue
            ok, tot = sum(r["giusti"] for r in rs), sum(r["totale"] for r in rs)
            full = sum(r["tutto_giusto"] for r in rs)
            avg = lambda k: round(sum(r[k] for r in rs) / len(rs))  # noqa: E731
            stopped = sum(len(r.get("fermate", [])) for r in rs)
            lines.append(f"| {name} | {mode} | {ok}/{tot} ({ok / max(tot, 1):.0%}) | {full}/{len(rs)} | {stopped} | {avg('contesto_max')} | "
                         f"{avg('token_letti')} | {avg('secondi')} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modello", default="qwen3.5:4b")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--compiti", default="spese,posta,preventivo")
    ap.add_argument("--quante", type=int, default=4, help="lavori diversi per compito")
    ap.add_argument("--seme", type=int, default=7)
    ap.add_argument("--num-ctx", type=int, default=32768)
    ap.add_argument("--ragiona", action="store_true")
    ap.add_argument("--uscita", default="procedure.json")
    args = ap.parse_args()
    model = Measured(args.modello, args.url, args.num_ctx, args.ragiona)
    procedure.MAX_CONTEXT_CHARS = 120_000  # il modo unico deve poter leggere tutto (circa 30 mila token)
    procedure.MAX_FILE_CHARS = 60_000
    results = []
    folder = Path(tempfile.mkdtemp(prefix="procedure-"))
    for name in [n.strip() for n in args.compiti.split(",") if n.strip()]:
        for i in range(args.quante):
            task = TASKS[name](random.Random(f"{args.seme}-{name}-{i}"))
            for mode in ("unico", "fasi"):
                r = run_one(task, mode, folder / f"{i}", model)
                r.update({"compito": name, "lavoro": i})
                results.append(r)
                print(f"{name} {i} {mode}: {r['giusti']}/{r['totale']} in {r['secondi']} s, contesto max {r['contesto_max']}"
                      + (f" — {r['errore'][:120]}" if r["errore"] else ""), flush=True)
                Path(args.uscita).write_text(json.dumps({"modello": args.modello, "ragiona": args.ragiona, "risultati": results},
                                                        ensure_ascii=False, indent=1))
    print("\n" + table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
