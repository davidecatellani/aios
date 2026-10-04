"""Gli esempi per addestrare Laya, il modello decisionale di Nova: python addestramento/dati_laya.py --uscita dati-laya

Le domande sono quelle che Nova fa davvero (smistatore.py, voice.py, giochi.py), con le stesse parole:
- «ambito»: di che ambito è la richiesta (agenda, posta, file…);
- «azione»: quale azione fare tra quelle dell'ambito;
- «destinatario»: la frase sentita dal microfono è per Nova o no (persone che parlano, TV, canzoni);
- «ricarica»: durante un gioco in secondo piano, ricaricare Nova adesso?
- «percorso»: comando da eseguire, risposta veloce a parole, o ragionamento (il 4B pensa prima di rispondere);
- «categoria» e «importanza» di una mail (esempi_posta.py).
Ogni riga: {"state": testo, "questions": {...}, "gold": {domanda: {"probabilities": {...}}}} (formato di
laya.train). Le frasi dei comandi vengono da dati.py (frasi.py, catalogo, banco di prova); quelle «non per
Nova» sono qui sotto. Escono laya.jsonl (addestramento) e prova-laya.jsonl (frasi tenute da parte).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aios_copilot.giochi import RELOAD_QUESTION, reload_state  # noqa: E402
from aios_copilot.smistatore import NO_ACTION, QUESTION_AZIONE, QUESTION_PERCORSO_FULL, action_criteria  # noqa: E402
from aios_copilot.voice import ADDRESSED_CRITERIA, ADDRESSED_QUESTION  # noqa: E402

# Frasi che il microfono sente ma non sono per Nova: persone che parlano tra loro, TV e film, canzoni, pezzi.
NON_PER_NOVA = [
    "e poi siamo andati al mare con i bambini", "passami il sale per favore", "hai visto la partita ieri sera?",
    "no guarda, non è così che si fa", "domani vado dal dentista alle nove", "mamma mia che caldo oggi",
    "ma tu ci credi a quello che ha detto?", "allora ci vediamo alle otto davanti al bar", "che buona questa pasta",
    "dai sbrigati che facciamo tardi", "ti ho detto mille volte di chiudere la porta", "sì sì, arrivo subito",
    "secondo me ha ragione lei", "hai comprato il pane?", "guarda che bel cane", "non ho più voglia di lavorare oggi",
    "stasera c'è il film con quell'attore", "mi passi il telecomando?", "quanto costava alla fine la macchina?",
    "ieri ho incontrato Marco al supermercato", "aspetta che finisco di mangiare", "che ore fa il tuo orologio?",
    "il mio capo mi ha chiamato tre volte", "andiamo a fare una passeggiata?", "ha chiamato tua sorella prima",
    "devi studiare per la verifica di domani", "non trovo più le chiavi della macchina", "ma che dici, dai",
    "il commissario entrò nella stanza senza dire una parola", "signore e signori, benvenuti alla finale",
    "e adesso la linea passa alla redazione sportiva", "previsioni del tempo: domani sole su tutta la penisola",
    "ti amo più della mia vita, disse lei piangendo", "fermo, polizia! Mani in alto", "goal! Incredibile, che rete",
    "nel blu dipinto di blu", "volare oh oh, cantare oh oh oh", "e la luna bussò alle porte del buio",
    "con l'offerta di oggi hai due mesi gratis", "acquista ora e risparmia il venti per cento",
    "eh", "boh non lo so", "cioè tipo", "comunque", "allora niente", "va be'", "ok ok ok", "ma dai",
    "no", "aspetta un attimo", "e quindi?", "ah ecco", "sì però", "vabbè lasciamo stare",
    "ciao amore, com'è andata a scuola?", "pronto? sì, sono io, dimmi pure", "ti richiamo dopo che sono in macchina",
    "hai messo il cappotto? fuori fa freddo", "ricordati di chiamare la nonna", "mettiti le scarpe che usciamo",
    "spegni la luce quando esci dalla stanza", "apri tu la porta che io ho le mani occupate",
    "alza la voce che non ti sento", "abbassa la musica che il bimbo dorme", "chiudi la finestra per favore",
]
# alcuni di questi sono comandi detti a un'altra persona: senza «Nova» davanti li decide il contesto, ma
# spesso nelle case si dicono tra persone; il modello impara a non prenderli per sicuri (probabilità morbide)
AMBIGUI = {"ricordati di chiamare la nonna", "spegni la luce quando esci dalla stanza",
           "apri tu la porta che io ho le mani occupate", "alza la voce che non ti sento",
           "abbassa la musica che il bimbo dorme", "chiudi la finestra per favore", "mettiti le scarpe che usciamo"}

# Richieste che vogliono un ragionamento (il modello pensa prima di rispondere) …
RAGIONAMENTO = [
    "quanto fa 17 per 23 più 145 diviso 5?", "se un treno parte alle 8:40 e viaggia 2 ore e 35 minuti a che ora arriva?",
    "scrivimi una funzione python che ordina una lista di dizionari per data",
    "spiegami la differenza tra un mutuo a tasso fisso e uno variabile e quale conviene adesso",
    "aiutami a scrivere una lettera di reclamo formale al condominio per i rumori notturni",
    "ho 1200 euro al mese, affitto 550, bollette 120, spesa 300: quanto posso risparmiare e come?",
    "confronta pro e contro di comprare un'auto elettrica o ibrida per 15000 km l'anno",
    "organizzami un itinerario di 5 giorni in Sicilia con tappe e spostamenti",
    "perché il cielo è blu? spiegamelo bene", "risolvi l'equazione 3x al quadrato meno 12 uguale zero",
    "correggi questo codice: for i in range(10) print(i)", "scrivi un tema di 500 parole sull'inquinamento",
    "trova l'errore nel mio ragionamento: tutti i gatti sono animali, il mio cane è un animale, quindi è un gatto",
    "fammi un piano di allenamento di tre mesi per correre 10 km", "calcola lo sconto del 35% su 89,90 euro e poi l'iva",
    "come funziona la blockchain? spiegalo passo passo", "traduci e adatta questo contratto in inglese formale",
    "riassumi in modo dettagliato i vantaggi e i rischi dell'intelligenza artificiale",
    "un indovinello: ho le chiavi ma non apro porte, cosa sono?", "pianifica il budget per un matrimonio di 80 invitati",
    "scrivimi uno script bash che fa il backup della cartella documenti ogni giorno",
    "analizza questa frase e dimmi se è grammaticalmente corretta: se io avrei saputo sarei venuto",
    "quanti giorni mancano dal 3 marzo al 18 agosto?", "dimostra che la radice di 2 è irrazionale",
    "scrivi una mail diplomatica al mio capo per chiedere un aumento", "aiutami a scegliere tra due offerte di lavoro",
]
# … e richieste da risposta veloce a parole, senza azioni sul computer
RISPOSTA = [
    "ciao Nova", "come stai?", "grazie mille", "buongiorno", "chi sei?", "cosa sai fare?", "che bello!",
    "raccontami una barzelletta", "qual è la capitale della Francia?", "come si dice gatto in inglese?",
    "dammi un consiglio per dormire meglio", "cosa posso cucinare con uova e zucchine?", "sei simpatica",
    "quanti anni ha la terra più o meno?", "scrivimi una frase di auguri per mia sorella", "buonanotte",
    "ok perfetto", "che significa resilienza?", "chi ha scritto i promessi sposi?", "dimmi una curiosità",
]

GIOCHI = ["steam_app_570", "steam_app_1091500", "eldenring.exe", "minecraft", "retroarch", "supertuxkart"]


def soft(gold: str, options: list[str], sure: float = 0.92) -> dict[str, Any]:
    rest = (1 - sure) / max(len(options) - 1, 1)
    return {"probabilities": {o: (sure if o == gold else rest) for o in options}}


def text_of(prompt: str) -> str:
    return prompt.split("<|im_start|>user\n", 1)[1].split("<|im_end|>", 1)[0]


def build(seed: int = 5) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import dati

    rng = random.Random(seed)
    domains, tools = dati.nova_tools()
    from aios_copilot import smistatore as sm

    judge = sm.Smistatore([sm.Domain(n, d) for n, d in sm.DOMAINS])
    q_ambito = judge.question_ambito()
    q_dest = {"type": "choice", "instructions": ADDRESSED_QUESTION, "criteria": ADDRESSED_CRITERIA}
    rows = dati.build(per_template=6)["smistamento"]
    by_text: dict[str, dict[str, Any]] = {}
    for r in rows:
        text = text_of(r["prompt"])
        row = by_text.setdefault(text, {"state": text, "questions": {}, "gold": {}, "parte": r["parte"]})
        if r["compito"] == "ambito":
            row["questions"]["ambito"] = q_ambito
            row["gold"]["ambito"] = soft(r["risposta"], list(q_ambito["criteria"]))
            row["questions"]["destinatario"] = q_dest
            row["gold"]["destinatario"] = soft("richiesta", list(ADDRESSED_CRITERIA))
            row["questions"]["percorso"] = QUESTION_PERCORSO_FULL
            route = "risposta" if r["risposta"] == "chiacchiera" else "azione"
            row["gold"]["percorso"] = soft(route, list(QUESTION_PERCORSO_FULL["criteria"]), 0.8 if route == "risposta" else 0.92)
        elif r["compito"] == "azione" and r["risposta"] != NO_ACTION:
            names = [n for n in r["opzioni"] if n != NO_ACTION]
            crit = action_criteria({n: tools[n] for n in names if n in tools})
            row["questions"]["azione"] = {"type": "choice", "instructions": QUESTION_AZIONE, "criteria": crit}
            row["gold"]["azione"] = soft(r["risposta"], list(crit))
    train = [r for r in by_text.values() if r["parte"] == "addestra"]
    test = [r for r in by_text.values() if r["parte"] == "prova"]
    # non per Nova: con piccole variazioni; uno su cinque tenuto da parte
    for i, text in enumerate(NON_PER_NOVA):
        sure = 0.7 if text in AMBIGUI else 0.92
        for k in range(3 if i % 5 else 1):
            t = text if k == 0 else (text.capitalize() + rng.choice([".", "!", "", "?"]))
            row = {"state": t, "questions": {"destinatario": q_dest},
                   "gold": {"destinatario": soft("altro", list(ADDRESSED_CRITERIA), sure)}}
            (test if i % 5 == 0 else train).append(row)
    # percorso: ragionamento o risposta veloce (anche con l'ambito «chiacchiera»)
    for kind, texts in (("ragionamento", RAGIONAMENTO), ("risposta", RISPOSTA)):
        for i, text in enumerate(texts):
            for k in range(3 if i % 5 else 1):
                t = text if k == 0 else rng.choice(["Nova, ", "senti, ", "", "ehi "]) + text
                row = {"state": t, "questions": {"ambito": q_ambito, "percorso": QUESTION_PERCORSO_FULL},
                       "gold": {"ambito": soft("chiacchiera", list(q_ambito["criteria"]), 0.85),
                                "percorso": soft(kind, list(QUESTION_PERCORSO_FULL["criteria"]))}}
                (test if i % 5 == 0 else train).append(row)
    # la posta: categoria e importanza
    import esempi_posta

    mail_train, mail_test = esempi_posta.rows()
    train += mail_train
    test += mail_test
    # durante un gioco: ricaricare Nova? (fuori dal gioco da poco: no; da parecchio e memoria libera: sì)
    for n in range(260):
        away = rng.choice([rng.uniform(10, 90), rng.uniform(90, 240), rng.uniform(240, 1800)])
        free = rng.choice([rng.uniform(0.3, 1.5), rng.uniform(1.5, 6.0)])
        clock = f"{rng.randint(0, 23):02d}:{rng.choice([0, 15, 30, 45]):02d}"
        if away >= 600 or (away >= 180 and free >= 2.5):
            gold, sure = "si", (0.9 if away >= 300 else 0.75)
        elif away < 90:
            gold, sure = "no", 0.9
        else:
            gold, sure = "no", 0.65
        row = {"state": reload_state(rng.choice(GIOCHI), away, free, clock), "questions": {"ricarica": RELOAD_QUESTION},
               "gold": {"ricarica": soft(gold, ["si", "no"], sure)}}
        (test if n % 6 == 0 else train).append(row)
    for r in train + test:
        r.pop("parte", None)
    rng.shuffle(train)
    return train, test


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uscita", default="dati-laya")
    args = ap.parse_args()
    out = Path(args.uscita)
    out.mkdir(parents=True, exist_ok=True)
    train, test = build()
    for name, rows in (("laya.jsonl", train), ("prova-laya.jsonl", test)):
        with open(out / name, "w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        counts: dict[str, int] = {}
        for r in rows:
            for q in r["questions"]:
                counts[q] = counts.get(q, 0) + 1
        print(f"{name}: {len(rows)} frasi, domande {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
