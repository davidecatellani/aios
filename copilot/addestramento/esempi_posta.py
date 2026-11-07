"""Mail finte per insegnare a Laya la categoria e l'importanza della posta (domande «categoria» e «importanza»).

Ogni mail è descritta a Laya come la vede Nova (mail_state in aios_copilot/mail/classify.py, da dove vengono
anche le domande): mittente, se è un
invio di massa o automatico, quante volte gli hai scritto, oggetto e inizio del testo.
"""

from __future__ import annotations

import random
from typing import Any

from aios_copilot.mail.classify import CATEGORIE_LAYA, LIVELLI

PERSONE = ["Giulia Rossi", "Marco Bianchi", "Luca Ferrari", "Sara Colombo", "Paolo Ricci", "Chiara Marino",
           "Andrea Greco", "Francesca Bruno", "Matteo Gallo", "Elena Conti", "zia Carla", "mamma", "Roberto",
           "Silvia De Luca", "Davide Esposito", "Anna Romano"]
DOMINI_PERSONALI = ["gmail.com", "libero.it", "hotmail.it", "yahoo.it", "icloud.com", "outlook.com"]
AZIENDE = ["studiorossi.it", "acme-srl.com", "tecnoservice.it", "costruzionibianchi.it", "medialab.eu",
           "farmaciacentrale.it", "comune.milano.it", "unimi.it"]
SERVIZI = [("Amazon", "amazon.it"), ("Netflix", "netflix.com"), ("Enel Energia", "enel.it"), ("TIM", "tim.it"),
           ("Spotify", "spotify.com"), ("PayPal", "paypal.it"), ("Trenitalia", "trenitalia.it"),
           ("Iliad", "iliad.it"), ("Zalando", "zalando.it"), ("IKEA", "ikea.it"), ("Booking.com", "booking.com"),
           ("Fastweb", "fastweb.it"), ("Google", "google.com"), ("Apple", "apple.com"), ("Intesa Sanpaolo", "intesasanpaolo.com")]

# (oggetto, testo, importanza 0-4) per categoria; {p} persona, {s} servizio, {g} giorno, {n} numero
MODELLI: dict[str, list[tuple[str, str, int]]] = {
    "personali": [
        ("Cena sabato?", "Ciao! Sabato sera siamo liberi, venite a cena da noi? Porta anche i bambini.", 2),
        ("foto delle vacanze", "Ecco le foto di Rimini che ti avevo promesso, la più bella è quella al tramonto.", 2),
        ("Auguri!!", "Tanti auguri di buon compleanno! Ci vediamo presto per festeggiare.", 2),
        ("Re: il libro", "L'ho finito ieri sera, bellissimo. Te lo riporto {g}.", 1),
        ("ciao", "Come stai? È tanto che non ci sentiamo, fammi sapere come va.", 2),
        ("la nonna", "Ti scrivo perché la nonna è in ospedale da stamattina, chiamami appena puoi.", 4),
        ("Urgente: chiamami", "Ho bisogno di parlarti subito, è successa una cosa. Chiamami appena leggi.", 4),
        ("matrimonio", "Ti confermo che il matrimonio è il {n} giugno, ti mando l'invito a breve.", 3),
        ("Re: prestito trapano", "Certo, passa pure a prenderlo quando vuoi, sono a casa tutto il pomeriggio.", 1),
        ("ricetta tiramisù", "Come promesso ecco la ricetta: mascarpone, uova, savoiardi e caffè.", 1),
        ("chiavi di casa", "Hai tu le chiavi di casa della mamma? Domani mattina mi servono assolutamente.", 3),
        ("gita in montagna", "Domenica pensavamo di andare in montagna, ti va di venire?", 2),
    ],
    "lavoro": [
        ("Riunione di progetto {g}", "Ti confermo la riunione di {g} alle 10 per fare il punto sul progetto.", 2),
        ("Preventivo n. {n}", "In allegato il preventivo richiesto per la fornitura, resto a disposizione.", 2),
        ("URGENTE: consegna entro oggi", "Il cliente chiede la consegna entro oggi alle 17, riesci a mandarmi il file?", 4),
        ("Contratto da firmare", "Ti mando il contratto da firmare e rispedire entro venerdì.", 3),
        ("Report mensile", "Ecco il report di settembre con i numeri delle vendite.", 1),
        ("Re: ticket {n}", "Abbiamo risolto il problema segnalato nel ticket, puoi verificare?", 2),
        ("Scadenza dichiarazione", "Ricordo che la scadenza per i documenti è il {n} del mese, mancano ancora le ricevute.", 3),
        ("cambio orario turno", "Da lunedì il tuo turno passa al pomeriggio, fammi sapere se ci sono problemi.", 3),
        ("Verbale riunione", "In allegato il verbale della riunione di ieri.", 1),
        ("Richiesta ferie approvata", "La tua richiesta di ferie dal {n} agosto è stata approvata.", 2),
        ("Colloquio", "Saremmo lieti di incontrarla per un colloquio {g} alle 15 nella nostra sede.", 4),
        ("fattura cliente da sollecitare", "Il cliente non ha ancora pagato la fattura di luglio, puoi mandare un sollecito?", 3),
    ],
    "ricevute": [
        ("Conferma del tuo ordine n. {n}", "Grazie per il tuo acquisto su {s}. Il tuo ordine è stato confermato e verrà spedito a breve.", 1),
        ("La tua ricevuta di {s}", "Abbiamo addebitato 12,99 € sul tuo metodo di pagamento per l'abbonamento mensile.", 1),
        ("Fattura disponibile", "La bolletta di {s} di settembre è disponibile nell'area clienti: importo 84,20 €.", 2),
        ("Pagamento ricevuto", "Hai inviato un pagamento di 45,00 € a {p}.", 1),
        ("Il tuo abbonamento si rinnova il {n}", "Il tuo abbonamento {s} si rinnoverà automaticamente.", 2),
        ("Biglietto per il viaggio", "Ecco il tuo biglietto: Milano Centrale - Roma Termini, carrozza 7 posto 12A.", 3),
        ("Prenotazione confermata", "La tua prenotazione presso Hotel Bellavista è confermata dal {n} luglio.", 3),
        ("Ordine spedito", "Il tuo pacco è in viaggio e arriverà {g}.", 1),
        ("Pagamento non riuscito", "Non siamo riusciti ad addebitare il pagamento del tuo abbonamento {s}: aggiorna il metodo di pagamento.", 3),
        ("Bolletta in scadenza", "Ti ricordiamo che la bolletta {s} scade il {n}: paga in tempo per evitare more.", 3),
    ],
    "newsletter": [
        ("Solo oggi: -50% su tutto", "Approfitta delle offerte {s}: sconti fino al 50% solo per 24 ore!", 0),
        ("Le novità della settimana", "Ecco gli articoli più letti della settimana nella nostra newsletter.", 0),
        ("Black Friday anticipato", "Offerte imperdibili ti aspettano, scopri subito i prodotti in promozione.", 0),
        ("Ti abbiamo riservato un coupon", "Usa il codice BENVENUTO10 per uno sconto del 10% sul prossimo ordine.", 0),
        ("Nuovi arrivi autunno", "Scopri la nuova collezione: maglioni, giacche e stivali.", 0),
        ("Il meglio di settembre", "I film e le serie più visti del mese, scelti per te.", 0),
        ("Webinar gratuito", "Iscriviti al nostro webinar gratuito sul marketing digitale.", 0),
        ("Ultimi giorni di saldi", "Ultimi giorni per approfittare dei saldi, non perderli!", 0),
        ("Notizie del giorno", "Politica, economia, sport: le notizie principali di oggi.", 0),
        ("Ricette della settimana", "Cinque ricette veloci per cene in famiglia.", 0),
    ],
    "notifiche": [
        ("Il tuo codice di verifica", "Il tuo codice di verifica è {n}. Non condividerlo con nessuno.", 2),
        ("Nuovo accesso al tuo account", "Abbiamo rilevato un nuovo accesso al tuo account {s} da un dispositivo Windows a Napoli. Se non sei stato tu, cambia subito la password.", 4),
        ("Avviso di sicurezza", "Accesso sospetto bloccato: qualcuno ha provato a entrare nel tuo account.", 4),
        ("Hai un nuovo follower", "{p} ha iniziato a seguirti.", 0),
        ("Promemoria: evento domani", "Ti ricordiamo l'evento di domani alle 18.", 2),
        ("Backup completato", "Il backup automatico del tuo telefono è stato completato.", 0),
        ("Spazio di archiviazione quasi pieno", "Hai usato il 95% dello spazio del tuo account {s}.", 2),
        ("Aggiornamento dei termini di servizio", "Abbiamo aggiornato i nostri termini di servizio.", 0),
        ("Il pacco è stato consegnato", "Il tuo pacco è stato consegnato al portiere.", 1),
        ("Password modificata", "La password del tuo account {s} è stata modificata. Se non sei stato tu contattaci.", 3),
        ("Qualcuno ha commentato la tua foto", "{p} ha commentato: bellissima!", 0),
    ],
}
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "domani", "dopodomani"]


def mail(rng: random.Random, cat: str, subject: str, body: str) -> dict[str, Any]:
    person = rng.choice(PERSONE)
    service, sdomain = rng.choice(SERVIZI)
    fill = {"p": person, "s": service, "g": rng.choice(GIORNI), "n": rng.randint(2, 28) if "{n}" in subject + body and
            rng.random() < .5 else rng.randint(1000, 99999)}
    subject, body = subject.format(**fill), body.format(**fill)
    if cat == "personali":
        local = person.lower().replace(" ", ".").replace("zia.", "").replace("'", "")
        sender, bulk, auto, sent = f"{person} <{local}@{rng.choice(DOMINI_PERSONALI)}>", False, False, rng.choice([0, 2, 5, 14])
    elif cat == "lavoro":
        local = person.split()[0].lower()
        sender, bulk, auto, sent = f"{person} <{local}@{rng.choice(AZIENDE)}>", False, False, rng.choice([0, 1, 6, 30])
    elif cat == "newsletter":
        sender, bulk, auto, sent = f"{service} <{rng.choice(['news', 'offerte', 'newsletter', 'marketing'])}@{sdomain}>", True, True, 0
    else:
        sender = f"{service} <{rng.choice(['no-reply', 'noreply', 'notifiche', 'info', 'account'])}@{sdomain}>"
        bulk, auto, sent = rng.random() < .2, True, 0
    return {"sender": sender, "subject": subject, "body": body, "bulk": bulk, "automatic": auto, "sent_to_count": sent}


def rows(seed: int = 11) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from aios_copilot.mail.classify import mail_questions, mail_state

    rng = random.Random(seed)
    train: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    questions = mail_questions()
    for cat, models in MODELLI.items():
        for i, (subject, body, level) in enumerate(models):
            for k in range(10):
                m = mail(rng, cat, subject, body)
                lvl = level
                if cat in ("personali", "lavoro") and m["sent_to_count"] >= 5 and lvl < 4 and rng.random() < .6:
                    lvl += 1  # le persone a cui scrivi spesso contano di più
                row = {"state": mail_state(**m), "questions": questions,
                       "gold": {"categoria": soft_pick(cat, list(CATEGORIE_LAYA)),
                                "importanza": {"probabilities": level_probs(lvl)}}}
                (test if i % 4 == 0 else train).append(row)  # un modello su quattro mai visto in addestramento
    return train, test


def soft_pick(gold: str, options: list[str], sure: float = 0.9) -> dict[str, Any]:
    rest = (1 - sure) / (len(options) - 1)
    return {"probabilities": {o: sure if o == gold else rest for o in options}}


def level_probs(level: int) -> dict[str, float]:
    p = [0.0] * len(LIVELLI)
    p[level] = 0.7
    for d in (-1, 1):
        if 0 <= level + d < len(LIVELLI):
            p[level + d] = 0.15
    total = sum(p)
    return {str(i): v / total for i, v in enumerate(p)}
