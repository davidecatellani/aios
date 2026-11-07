"""La misura dei livelli dei documenti (aios_copilot/bollette.py): «quali bollette devo pagare?» e «quanto ho speso
di luce quest'anno?» letti dal bronze (il modello legge tutti i documenti mentre l'utente aspetta) o dal gold (il
modello li ha letti prima, in sottofondo, uno alla volta; risponde il codice).

    python addestramento/bollette_prova.py --modello qwen3.5:4b --quante 3

Ogni cartella: 12 bollette e fatture inventate (luce, gas, acqua, telefono, internet, TARI) con tre impaginazioni
diverse, numeri che distraggono (consumi, codici, IVA, importo annuo e rata), addebiti automatici, scadenze già
passate, vicine e lontane; più 5 documenti che non sono bollette (uno con importi e una «rata»). La risposta
giusta è nota, i controlli sono automatici.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aios_copilot import bollette  # noqa: E402
from aios_copilot.fileindex import FileIndex  # noqa: E402
from aios_copilot.tabelle import MONTHS, fmt  # noqa: E402

NOW = datetime(2026, 10, 5, 10, 0)
DAYS = 30
ENERGY = [("Enel Energia", "luce", "energia elettrica"), ("A2A Energia", "luce", "energia elettrica"),
          ("Hera Comm", "gas", "gas naturale"), ("Eni Plenitude", "gas", "gas naturale")]
TELCO = [("TIM", "telefono", "TIM Unica"), ("Fastweb", "internet", "Fastweb Casa"), ("Vodafone", "telefono", "Vodafone Giga")]
OTHERS = {
    "ricetta.txt": "Torta di mele della nonna\nIngredienti: 3 mele, 200 g di farina, 150 g di zucchero, 3 uova, 1 bustina di lievito.\n"
                   "Cuocere a 180 gradi per 40 minuti.",
    "verbale_condominio.txt": "Verbale dell'assemblea di condominio del 12 settembre 2026\nPresenti 8 condomini su 12.\n"
                              "Si approva il preventivo per la pittura delle scale: 4.800,00 € da dividere in millesimi.\n"
                              "La prima rata sarà comunicata dall'amministratore con apposito avviso. Fondo cassa: 1.250,00 €.",
    "manuale_lavatrice.txt": "Lavatrice Candy CS 1482 - Istruzioni\nPrima del primo lavaggio togliere i bulloni di trasporto.\n"
                             "Programma cotone 60 gradi: durata 3 ore e 20 minuti. Centrifuga massima 1400 giri.",
    "lettera.txt": "Cara nonna,\nti scrivo da Bologna. Qui va tutto bene, a novembre vengo a trovarti.\nUn abbraccio, Giulia",
    "curriculum.txt": "Giulia Bianchi - Curriculum\nEsperienze: impiegata amministrativa dal 2019.\nLingue: inglese B2, spagnolo A2.",
}


NOTES = ["Riunione con il fornitore spostata a giovedì.", "Ricordarsi di chiamare l'idraulico per il rubinetto.",
         "Il corso di yoga ricomincia a ottobre il martedì sera.", "Idee per le vacanze: Puglia o Sardegna a giugno.",
         "La macchina va portata a fare il tagliando dopo i 30.000 km.", "Comprare il regalo per il compleanno di Marco.",
         "La palestra chiude per lavori la prima settimana di agosto.", "Leggere il libro consigliato da Anna sul giardinaggio.",
         "Prenotare il ristorante per la cena di classe.", "Il vicino chiede se possiamo ritirare un pacco per lui."]


def notes(rng: random.Random, n: int) -> dict[str, str]:
    """Appunti qualsiasi: in una cartella vera i documenti che non sono bollette sono la maggior parte."""
    return {f"appunti_{k}.txt": "Appunti\n" + "\n".join(rng.sample(NOTES, 6)) for k in range(n)}


def euro(rng: random.Random, a: float, b: float) -> float:
    return round(rng.uniform(a, b), 2)


def it_date(d: date, words: bool) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}" if words else f"{d:%d/%m/%Y}"


def energy_bill(rng: random.Random, issuer: str, service: str, word: str, doc: date, due: date, auto: bool) -> tuple[str, float]:
    parts = [euro(rng, 20, 60), euro(rng, 8, 25), euro(rng, 5, 20), euro(rng, 1, 8)]
    vat = round(sum(parts) * 0.10, 2)
    total = round(sum(parts) + vat, 2)
    start = doc.replace(day=1) - timedelta(days=60)
    pay = (f"Pagamento con domiciliazione bancaria: l'importo sarà addebitato sul suo conto il {it_date(due, False)}." if auto else
           "Può pagare con l'avviso pagoPA allegato presso banche, uffici postali e tabaccherie.")
    text = (f"{issuer}\nBolletta {word} n. {rng.randint(100000, 999999)}/{doc.year} del {it_date(doc, False)}\n"
            f"Codice cliente: {rng.randint(1000000, 9999999)}   {'POD IT001E' if service == 'luce' else 'PDR '}{rng.randint(10**7, 10**8)}\n"
            f"Periodo di fornitura: dal {it_date(start, False)} al {it_date(start + timedelta(days=59), False)}\n"
            f"Consumi fatturati: {rng.randint(180, 900)} {'kWh' if service == 'luce' else 'Smc'}\n"
            f"Spesa per la materia {'energia' if service == 'luce' else 'gas naturale'}: {fmt(parts[0])} €\n"
            f"Spesa per il trasporto e la gestione del contatore: {fmt(parts[1])} €\nSpesa per oneri di sistema: {fmt(parts[2])} €\n"
            f"Imposte: {fmt(parts[3])} €\nIVA 10%: {fmt(vat)} €\nTotale da pagare: {fmt(total)} €\n"
            f"Scadenza: {it_date(due, False)}\n{pay}\n")
    return text, total


def telco_bill(rng: random.Random, issuer: str, service: str, offer: str, doc: date, due: date, auto: bool) -> tuple[str, float]:
    fee, extra = euro(rng, 19, 39), euro(rng, 0, 12)
    total = round(fee + extra, 2)
    pay = ("Modalità di pagamento: addebito diretto SEPA (SDD) sul conto corrente indicato." if auto else
           "Modalità di pagamento: bollettino postale allegato.")
    text = (f"{issuer} S.p.A.\nFattura n. {chr(65 + rng.randint(0, 25))}{rng.randint(10**8, 10**9)}\n"
            f"Data emissione: {it_date(doc, True)}\nPeriodo di riferimento: {MONTHS[doc.month - 1]} {doc.year}\n"
            f"Canone mensile offerta {offer}: {fmt(fee)} €\nTraffico e servizi extra: {fmt(extra)} €\n"
            f"Totale fattura (IVA inclusa): {fmt(total)} €\nDa pagare entro il {it_date(due, True)}\n{pay}\n")
    return text, total


def water_or_tari(rng: random.Random, kind: str, doc: date, due: date) -> tuple[str, float]:
    if kind == "acqua":
        total = euro(rng, 40, 140)
        return (f"Acea Ato 2\nBolletta servizio idrico integrato del {it_date(doc, False)}\nMatricola contatore {rng.randint(10**6, 10**7)}\n"
                f"Consumo: {rng.randint(20, 90)} metri cubi\nQuota fissa: {fmt(euro(rng, 5, 15))} €\nImporto da pagare: {fmt(total)} €\n"
                f"Scadenza pagamento: {it_date(due, False)}\n"), total
    annual = euro(rng, 180, 420)
    rata = round(annual / 2, 2)
    return (f"COMUNE DI BOLOGNA - Servizio Tributi\nAVVISO DI PAGAMENTO TARI {doc.year}\nEmesso il {it_date(doc, False)}\n"
            f"Superficie: {rng.randint(50, 130)} mq - Componenti del nucleo: {rng.randint(1, 5)}\n"
            f"Importo annuo dovuto: {fmt(annual)} €\nRata 2 di 2: {fmt(rata)} € con scadenza {it_date(due, False)}\n"
            "Pagabile con modello F24 o pagoPA.\n"), rata


def make_folder(rng: random.Random) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """→ (nome file → testo, le bollette con la risposta giusta)."""
    files, bills = {**OTHERS, **notes(rng, 8)}, []
    # quando scadono: 2 già passate, 4 nei prossimi 30 giorni (2 con addebito), le altre più in là o con addebito
    plan = ["passata", "passata", "vicina", "vicina", "vicina-auto", "vicina", "lontana", "vicina-auto", "lontana", "passata",
            "vicina", "passata"]
    rng.shuffle(plan)
    kinds = ["energia"] * 6 + ["telco"] * 4 + ["acqua", "tari"]
    for n, (kind, when) in enumerate(zip(kinds, plan)):
        if when == "passata":
            due = NOW.date() - timedelta(days=rng.randint(10, 250))
        elif when.startswith("vicina"):
            due = NOW.date() + timedelta(days=rng.randint(1, DAYS - 1))
        else:
            due = NOW.date() + timedelta(days=rng.randint(DAYS + 5, 80))
        doc = min(due - timedelta(days=rng.randint(15, 25)), NOW.date() - timedelta(days=rng.randint(1, 6)))  # già arrivata
        auto = when.endswith("auto")
        if kind == "energia":
            issuer, service, word = ENERGY[n % 4]
            text, total = energy_bill(rng, issuer, service, word, doc, due, auto)
        elif kind == "telco":
            issuer, service, word = TELCO[n % 3]
            text, total = telco_bill(rng, issuer, service, word, doc, due, auto)
        else:
            issuer, service = ("Acea Ato 2", "acqua") if kind == "acqua" else ("Comune di Bologna", "rifiuti")
            text, total = water_or_tari(rng, kind, doc, due)
        name = f"{service}_{doc:%Y_%m}_{n}.txt"
        files[name] = text
        bills.append({"file": name, "emittente": issuer, "servizio": service, "data": doc, "scadenza": due, "totale": total,
                      "addebito": auto})
    return files, bills


def expected(bills: list[dict[str, Any]]) -> dict[str, Any]:
    end = NOW.date() + timedelta(days=DAYS)
    to_pay = sorted((b for b in bills if not b["addebito"] and NOW.date() <= b["scadenza"] <= end), key=lambda b: b["scadenza"])
    others = [b for b in bills if b not in to_pay]
    luce = round(sum(b["totale"] for b in bills if b["servizio"] == "luce" and b["data"].year == NOW.year), 2)
    return {"da_pagare": to_pay, "altre": others, "luce": luce}


def check_to_pay(text: str, exp: dict[str, Any]) -> list[tuple[str, bool]]:
    out = []
    for b in exp["da_pagare"]:
        word = b["emittente"].split()[0]
        out.append((f"{word} {fmt(b['totale'])} €", fmt(b["totale"]) in text and word.lower() in text.lower()))
    mine = {fmt(x["totale"]) for x in exp["da_pagare"]}
    gone = [fmt(b["totale"]) for b in exp["altre"] if not b["addebito"] and fmt(b["totale"]) not in mine]
    out.append(("nessuna bolletta già scaduta o lontana", not any(a in text for a in gone)))
    auto = [fmt(b["totale"]) for b in exp["altre"] if b["addebito"] and fmt(b["totale"]) not in mine]
    out.append(("quelle con addebito automatico non sono da pagare", all(_said_automatic(text, a) for a in auto)))
    pos = [text.find(fmt(b["totale"])) for b in exp["da_pagare"]]
    out.append(("in ordine di scadenza", all(p >= 0 for p in pos) and pos == sorted(pos)))
    return out


def _said_automatic(text: str, amount: str) -> bool:
    """Un importo con addebito automatico può comparire solo se accanto (prima, nella stessa sezione) si dice che si
    paga da solo."""
    for m in re.finditer(re.escape(amount), text):
        if not re.search(r"addebit|domicili|automatic|si pag\w* da sol", text[max(0, m.start() - 300):m.end() + 80], re.I):
            return False
    return True


def check_luce(text: str, exp: dict[str, Any]) -> list[tuple[str, bool]]:
    return [(f"luce nel {NOW.year}: {fmt(exp['luce'])} €", fmt(exp["luce"]) in text)]


Q_PAY = f"Quali bollette devo pagare nei prossimi {DAYS} giorni? Oggi è il {NOW:%d/%m/%Y}."
Q_LUCE = f"Quanto ho speso di luce nel {NOW.year}?"


def run_folder(i: int, model: Any, base: Path) -> dict[str, Any]:
    rng = random.Random(f"bollette-{i}")
    files, bills = make_folder(rng)
    exp = expected(bills)
    folder = base / f"cartella-{i}"
    folder.mkdir(parents=True)
    for name, text in files.items():
        (folder / name).write_text(text)
    docs = "\n\n".join(f"### {name}\n{text}" for name, text in sorted(files.items()))

    # bronze: il modello legge tutti i documenti a ogni domanda
    bronze: dict[str, Any] = {"attesa": 0.0, "controlli": [], "token_letti": 0, "risposte": []}
    for question, check in ((Q_PAY, check_to_pay), (Q_LUCE, check_luce)):
        model.calls = []
        start = time.monotonic()
        try:
            reply = model.chat([{"role": "system", "content": "Sei Nova, l'assistente di SoIA. Rispondi in italiano, con gli "
                                 "importi in euro come sono scritti nei documenti. Le bollette con domiciliazione o addebito "
                                 "diretto si pagano da sole: non sono da pagare."},
                                {"role": "user", "content": f"Ecco i miei documenti:\n\n{docs}\n\n{question}"}], [])
            text = reply.get("content") or ""
        except Exception as exc:
            text = f"(errore: {exc})"
        bronze["attesa"] += time.monotonic() - start
        bronze["controlli"] += check(text, exp)
        bronze["token_letti"] += sum(c["prompt"] for c in getattr(model, "calls", []))
        bronze["risposte"].append(text[:2000])

    # silver in sottofondo (l'indice vero, poi la lettura dei documenti), gold dal codice
    index = FileIndex(db_path=base / f"indice-{i}.db", roots=[folder], excluded=[])
    while index.has_work():
        index.step(time.time() + 5)
    facts = bollette.Facts(base / f"documenti-{i}.db")
    model.calls = []
    start = time.monotonic()
    read = facts.update(index, model, limit=100)
    background = time.monotonic() - start
    start = time.monotonic()
    pay_text = bollette.to_pay_text(facts, NOW, DAYS)
    luce_text = bollette.spent_text(facts, "luce", NOW.year, NOW)
    wait = time.monotonic() - start
    silver = [dict(zip(("path", "tipo", "servizio", "emittente", "scadenza", "totale", "addebito", "controllo"), r)) for r in
              facts.db.execute("SELECT path, tipo, servizio, emittente, scadenza, totale, addebito, controllo FROM documenti_fatti")]
    gold = {"attesa": round(wait, 3), "controlli": check_to_pay(pay_text, exp) + check_luce(luce_text, exp),
            "sottofondo": round(background, 1), "documenti_letti": read, "richieste_al_modello": len(getattr(model, "calls", [])),
            "risposte": [pay_text, luce_text], "silver": silver}
    bronze["attesa"] = round(bronze["attesa"], 1)
    return {"cartella": i, "giusta": {"da_pagare": [f"{b['emittente']} {fmt(b['totale'])} entro {b['scadenza']}" for b in exp["da_pagare"]],
                                      "luce": fmt(exp["luce"])}, "bronze": bronze, "gold": gold}


def table(results: list[dict[str, Any]]) -> str:
    lines = ["| livello | cartelle tutte giuste | controlli giusti | attesa dell'utente (due domande) | lavoro in sottofondo per cartella |",
             "|---|---|---|---|---|"]
    for level, name in (("bronze", "bronze (legge tutto mentre aspetti)"), ("gold", "gold (letto prima, risponde il codice)")):
        rs = [r[level] for r in results]
        ok = sum(sum(c for _, c in r["controlli"]) for r in rs)
        tot = sum(len(r["controlli"]) for r in rs)
        full = sum(all(c for _, c in r["controlli"]) for r in rs)
        wait = sum(r["attesa"] for r in rs) / max(len(rs), 1)
        extra = "—"
        if level == "gold":
            extra = (f"{sum(r['sottofondo'] for r in rs) / len(rs):.0f} s ({sum(r['richieste_al_modello'] for r in rs) / len(rs):.0f} "
                     "documenti letti dal modello, gli altri scartati dal codice)")
        lines.append(f"| {name} | {full}/{len(rs)} | {ok}/{tot} ({ok / max(tot, 1):.0%}) | {wait:.2f} s | {extra} |")
    return "\n".join(lines)


def main() -> int:
    from procedure_prova import Measured

    ap = argparse.ArgumentParser()
    ap.add_argument("--modello", default="qwen3.5:4b")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--quante", type=int, default=3)
    ap.add_argument("--num-ctx", type=int, default=16384)
    ap.add_argument("--uscita", default="bollette.json")
    args = ap.parse_args()
    model = Measured(args.modello, args.url, args.num_ctx, False)
    base = Path(tempfile.mkdtemp(prefix="bollette-"))
    results = []
    for i in range(args.quante):
        r = run_folder(i, model, base)
        results.append(r)
        b, g = r["bronze"], r["gold"]
        print(f"cartella {i}: bronze {sum(c for _, c in b['controlli'])}/{len(b['controlli'])} in {b['attesa']} s; "
              f"gold {sum(c for _, c in g['controlli'])}/{len(g['controlli'])} in {g['attesa']} s "
              f"(sottofondo {g['sottofondo']} s, {g['richieste_al_modello']} richieste)", flush=True)
        Path(args.uscita).write_text(json.dumps({"modello": args.modello, "risultati": results}, ensure_ascii=False, indent=1,
                                                default=str))
    print("\n" + table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
