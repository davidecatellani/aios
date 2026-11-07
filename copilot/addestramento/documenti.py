"""Documenti finti per insegnare al nucleo a leggere: bollette, scontrini, avvisi di pagamento, fatture.

Ogni documento è disegnato come immagine (come una foto o una scansione: un po' storto, rumore, carta non
bianca) e se ne conosce già tutto: il testo esatto e i campi. Così gli esempi sono perfetti e nessun
documento vero dell'utente esce mai dal PC. Nomi, aziende e indirizzi sono inventati.

    python addestramento/documenti.py --uscita dati-documenti --quanti 700

Escono le immagini e documenti.jsonl / prova-documenti.jsonl con, per ogni immagine, due compiti:
«campi» (JSON: tipo, emittente, numero, data, scadenza, totale) e «testo» (la trascrizione).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aios_copilot.nucleo import DOCUMENT_FIELDS, PROMPT_DOCUMENTO, PROMPT_TESTO  # noqa: E402

NOMI = ["Marco Rossi", "Giulia Bianchi", "Luca Ferrari", "Sara Esposito", "Davide Romano", "Chiara Colombo",
        "Francesco Ricci", "Elena Marino", "Andrea Greco", "Laura Bruno", "Paolo Gallo", "Anna Conti"]
VIE = ["Via Roma", "Via Garibaldi", "Corso Italia", "Via Mazzini", "Viale dei Mille", "Via Dante", "Piazza Verdi",
       "Via Manzoni", "Via Cavour", "Via XX Settembre"]
CITTA = [("Torino", "10121", "TO"), ("Milano", "20123", "MI"), ("Bologna", "40121", "BO"), ("Firenze", "50122", "FI"),
         ("Roma", "00184", "RM"), ("Napoli", "80133", "NA"), ("Bari", "70121", "BA"), ("Verona", "37121", "VR")]
ENERGIA = ["Energia Chiara S.p.A.", "Luce Facile S.r.l.", "Gas & Casa S.p.A.", "Nordelettrica S.p.A.",
           "Sole Verde Energia S.r.l.", "Fiamma Blu Gas S.p.A."]
NEGOZI = ["Supermercato Il Girasole", "Market Da Pino", "Alimentari Bellavista", "Farmacia San Marco",
          "Ferramenta Bianchi", "Panificio La Spiga", "Bar Centrale", "Ottica Visione"]
ENTI = ["Comune di {c}", "Agenzia Entrate-Riscossione", "Consorzio Acque {c}", "Polizia Municipale di {c}",
        "Istituto Comprensivo {c} 2", "ASL {c} Centro"]
MEDICI = ["Studio Dentistico Dott. {n}", "Dott.ssa {n} - Pediatra", "Centro Fisioterapia {c}", "Studio Medico Dott. {n}",
          "Laboratorio Analisi {c}"]
PRESTAZIONI = ["Visita specialistica", "Pulizia dentale", "Seduta di fisioterapia", "Esami del sangue",
               "Visita di controllo", "Ecografia addome", "Otturazione"]
ARTICOLI = ["PANE COMUNE", "LATTE INTERO 1L", "PASTA PENNE 500G", "MOZZARELLA", "MELE GOLDEN", "CAFFE MACINATO",
            "OLIO EXTRA VERGINE", "PASSATA POMODORO", "BISCOTTI", "ACQUA NAT 6X1,5L", "YOGURT BIANCO", "UOVA X6",
            "DETERSIVO PIATTI", "CARTA IGIENICA X4", "BANANE", "PROSCIUTTO COTTO", "TACHIPIRINA 500MG", "CEROTTI"]
CAUSALI = ["Tassa rifiuti (TARI) rata 2", "Servizio idrico 3° trimestre", "Violazione art. 7 Codice della Strada",
           "Mensa scolastica ottobre", "Ticket prestazione sanitaria", "Avviso bonario IRPEF"]
FONTS = ["DejaVuSans.ttf", "DejaVuSerif.ttf", "DejaVuSansMono.ttf", "LiberationSans-Regular.ttf",
         "LiberationSerif-Regular.ttf", "LiberationMono-Regular.ttf"]


def euro(x: float) -> str:
    s = f"{x:,.2f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def italian(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def make_document(rng: random.Random) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """→ (righe: [(stile, testo)], campi). Stile: «titolo», «grande», «normale», «piccolo», «destra»."""
    kind = rng.choice(["bolletta luce", "bolletta gas", "scontrino", "avviso di pagamento", "fattura medica"])
    city, cap, prov = rng.choice(CITTA)
    person = rng.choice(NOMI)
    addr = f"{rng.choice(VIE)} {rng.randint(1, 180)}, {cap} {city} ({prov})"
    issued = date(2025, 1, 1) + timedelta(days=rng.randrange(640))
    lines: list[tuple[str, str]] = []
    if kind.startswith("bolletta"):
        who = rng.choice(ENERGIA)
        number = f"{rng.randint(2025, 2026)}/{rng.randint(100000, 999999)}"
        due = issued + timedelta(days=rng.choice([15, 20, 21, 30]))
        start = (issued.replace(day=1) - timedelta(days=rng.choice([30, 60]))).replace(day=1)
        end = issued.replace(day=1) - timedelta(days=1)
        use = rng.randint(80, 900)
        total = round(use * rng.uniform(0.18, 0.42) + rng.uniform(8, 30), 2)
        unit = "kWh" if kind == "bolletta luce" else "Smc"
        lines += [("titolo", who), ("piccolo", f"P.IVA {rng.randint(10**10, 10**11 - 1)} - Servizio clienti 800 {rng.randint(100, 999)} {rng.randint(100, 999)}"),
                  ("normale", ""), ("grande", f"Bolletta {'energia elettrica' if unit == 'kWh' else 'gas naturale'}"),
                  ("normale", f"Intestatario: {person}"), ("normale", f"Indirizzo di fornitura: {addr}"),
                  ("normale", f"Fattura n. {number} del {italian(issued)}"),
                  ("normale", f"Periodo: dal {italian(start)} al {italian(end)}"),
                  ("normale", f"Consumo fatturato: {use} {unit}"), ("normale", ""),
                  ("grande", f"Totale da pagare: € {euro(total)}"), ("grande", f"Scadenza: {italian(due)}"),
                  ("piccolo", "Pagamento con domiciliazione bancaria o bollettino PagoPA")]
    elif kind == "scontrino":
        who = rng.choice(NEGOZI)
        number = f"{rng.randint(1, 9999):04d}-{rng.randint(1, 999):03d}"
        due = None
        items = rng.sample(ARTICOLI, rng.randint(3, 8))
        prices = [round(rng.uniform(0.5, 12), 2) for _ in items]
        total = round(sum(prices), 2)
        hour = f"{rng.randint(8, 20):02d}:{rng.randint(0, 59):02d}"
        lines += [("titolo", who), ("piccolo", addr), ("piccolo", f"P.IVA {rng.randint(10**10, 10**11 - 1)}"),
                  ("normale", "DOCUMENTO COMMERCIALE"), ("piccolo", "di vendita o prestazione"), ("normale", "")]
        lines += [("riga", f"{name}\t{euro(p)}") for name, p in zip(items, prices)]
        lines += [("normale", ""), ("grande", f"TOTALE COMPLESSIVO\t{euro(total)}"),
                  ("normale", f"Pagamento {rng.choice(['contante', 'elettronico'])}\t{euro(total)}"),
                  ("piccolo", f"{italian(issued)} {hour}  DOC.N. {number}")]
    elif kind == "avviso di pagamento":
        who = rng.choice(ENTI).format(c=city)
        number = "".join(str(rng.randint(0, 9)) for _ in range(18))
        due = issued + timedelta(days=rng.choice([30, 60, 90]))
        total = round(rng.uniform(25, 480), 2)
        lines += [("titolo", who), ("grande", "AVVISO DI PAGAMENTO pagoPA"), ("normale", ""),
                  ("normale", f"Destinatario: {person}"), ("normale", addr),
                  ("normale", f"Oggetto: {rng.choice(CAUSALI)}"), ("normale", f"Data emissione: {italian(issued)}"),
                  ("normale", f"Codice avviso: {number[:4]} {number[4:8]} {number[8:12]} {number[12:16]} {number[16:]}"),
                  ("normale", ""), ("grande", f"Importo: € {euro(total)}"), ("grande", f"Entro il {italian(due)}"),
                  ("piccolo", "Puoi pagare presso banche, uffici postali, tabaccherie e online")]
        number = f"{number[:4]} {number[4:8]} {number[8:12]} {number[12:16]} {number[16:]}"
    else:
        doctor = rng.choice(NOMI)
        who = rng.choice(MEDICI).format(n=doctor.split()[1], c=city)
        number = f"{rng.randint(1, 400)}/{issued.year}"
        due = None
        what = rng.choice(PRESTAZIONI)
        total = round(rng.choice([50, 60, 70, 80, 90, 100, 120, 150, 180]) + rng.choice([0, 0, 2]), 2)
        lines += [("titolo", who), ("piccolo", addr), ("piccolo", f"C.F./P.IVA {rng.randint(10**10, 10**11 - 1)}"),
                  ("normale", ""), ("grande", f"Fattura n. {number}"), ("normale", f"Data: {italian(issued)}"),
                  ("normale", f"Paziente: {person}"), ("normale", f"Prestazione: {what}"),
                  ("normale", "Operazione esente IVA art. 10 DPR 633/72"),
                  ("normale", ""), ("grande", f"Totale: € {euro(total)}"),
                  ("piccolo", "Spesa detraibile - pagamento tracciabile")]
    fields = {"tipo": kind, "emittente": who, "numero": number, "data": issued.isoformat(),
              "scadenza": due.isoformat() if due else "", "totale": euro(total)}
    return lines, fields


def transcript(lines: list[tuple[str, str]]) -> str:
    return "\n".join(text.replace("\t", " ") for _, text in lines if text.strip())


def font_path(name: str) -> str | None:
    for base in (Path("/usr/share/fonts"), Path("/usr/share/fonts/truetype")):
        found = list(base.rglob(name)) if base.exists() else []
        if found:
            return str(found[0])
    return None


def render(lines: list[tuple[str, str]], rng: random.Random, receipt: bool) -> Any:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    fonts = [f for f in (font_path(n) for n in FONTS) if f]
    face = rng.choice(fonts)
    width = rng.randint(330, 380) if receipt else rng.randint(560, 640)
    base = rng.randint(13, 16) if not receipt else rng.randint(13, 15)
    sizes = {"titolo": base + 7, "grande": base + 3, "normale": base, "riga": base, "piccolo": base - 3}
    height = 40 + sum(int(sizes.get(s, base) * 1.55) for s, _ in lines) + 30
    paper = tuple(rng.randint(236, 255) for _ in range(3))
    img = Image.new("RGB", (width, height), paper)
    draw = ImageDraw.Draw(img)
    ink = tuple(rng.randint(0, 60) for _ in range(3))
    y = 30
    for style, text in lines:
        size = sizes.get(style, base)
        font = ImageFont.truetype(face, size)
        if "\t" in text:
            left, right = text.split("\t", 1)
            draw.text((24, y), left, font=font, fill=ink)
            draw.text((width - 24 - draw.textlength(right, font=font), y), right, font=font, fill=ink)
        elif style == "titolo" or receipt:
            draw.text(((width - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=ink)
        else:
            draw.text((24, y), text, font=font, fill=ink)
        y += int(size * 1.55)
    # come una foto: sfondo, inclinazione, sfocatura, luce non uniforme
    margin = rng.randint(10, 50)
    bg = Image.new("RGB", (width + 2 * margin, height + 2 * margin), tuple(rng.randint(60, 200) for _ in range(3)))
    bg.paste(img, (margin, margin))
    img = bg.rotate(rng.uniform(-3, 3), expand=True, fillcolor=tuple(rng.randint(60, 200) for _ in range(3)))
    if rng.random() < 0.5:
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.2, 0.9)))
    scale = min(1.0, 800 / max(img.size))
    if scale < 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    return img


def build(out: Path, count: int, seed: int = 11, held_out: float = 0.1) -> None:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    (out / "img").mkdir(exist_ok=True)
    train, test = [], []
    for i in range(count):
        lines, fields = make_document(rng)
        img = render(lines, rng, receipt=fields["tipo"] == "scontrino")
        name = f"img/doc{i:05d}.jpg"
        img.save(out / name, quality=rng.randint(70, 92))
        rows = [{"immagine": name, "compito": "documento", "prompt": PROMPT_DOCUMENTO,
                 "risposta": json.dumps({k: fields[k] for k in DOCUMENT_FIELDS}, ensure_ascii=False)},
                {"immagine": name, "compito": "testo", "prompt": PROMPT_TESTO, "risposta": transcript(lines)}]
        (test if i < count * held_out else train).extend(rows)
    for fname, rows in (("documenti.jsonl", train), ("prova-documenti.jsonl", test)):
        with open(out / fname, "w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{fname}: {len(rows)} esempi")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uscita", default="dati-documenti")
    ap.add_argument("--quanti", type=int, default=700)
    args = ap.parse_args()
    build(Path(args.uscita), args.quanti)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
