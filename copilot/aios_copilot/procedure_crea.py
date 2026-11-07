"""Creare una procedura (docs/PROCEDURE.md): dalle fasi dette a parole, o da un modello pronto.

Nova chiede poche cose (cosa deve uscire, da dove prende i dati, quando parte, se vuoi vedere prima) e chiama
create(); i modelli pronti (MODELS) sono le procedure misurate in addestramento/procedure_prova.py: i conti, i
filtri e l'ordine li fa il codice (azione «tabella»), il modello classifica e scrive.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import procedure

INPUT_NOTE = ("Metti qui i file da elaborare a ogni giro (per esempio l'estratto conto del mese). "
              "La procedura li legge da questa cartella.\n")
CONFIG_NOTE = "Qui ci sono le regole che valgono sempre (stile, categorie, listino…): cambiale quando vuoi.\n"


def slug(text: str) -> str:
    words = re.findall(r"[a-zà-ù0-9]+", text.lower())
    return "_".join(words[:3]) or "fase"


def write(base: Path, name: str, purpose: str, stages: list[tuple[str, str]], files: dict[str, str] | None = None,
          when: str = "") -> procedure.Workspace:
    """Scrive la cartella della procedura (nomi originali di ICM). Non sovrascrive una procedura che c'è già."""
    name = re.sub(r"[/\\:*?\"<>|]+", " ", name).strip()[:60] or "Procedura"
    folder = base / name
    if folder.exists():
        raise FileExistsError(f"c'è già una procedura «{name}»")
    (folder / "input").mkdir(parents=True)
    (folder / "_config").mkdir()
    (folder / "input" / "LEGGIMI.txt").write_text(INPUT_NOTE)
    (folder / "_config" / "LEGGIMI.txt").write_text(CONFIG_NOTE)
    for rel, text in (files or {}).items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text.rstrip() + "\n")
    (folder / "CLAUDE.md").write_text(f"# {name}\n\n{purpose.strip()}\n\nI file da elaborare sono in input/, le regole in _config/.\n")
    plan = procedure.parse_when(when) if when else None
    (folder / "CONTEXT.md").write_text("# Le fasi\n\n" + "\n".join(f"{i}. {key}" for i, (key, _) in enumerate(stages, 1))
                                       + f"\n\nQuando: {procedure.describe_when(plan)}.\n")
    for key, contract in stages:
        (folder / "stages" / key).mkdir(parents=True)
        (folder / "stages" / key / "CONTEXT.md").write_text(contract.strip() + "\n")
    ws = procedure.Workspace(folder)
    if plan:
        procedure.set_schedule(ws, plan)
    return ws


def from_steps(steps: list[str]) -> list[tuple[str, str]]:
    """Le fasi dette a parole → i contratti: ogni fase legge il risultato della precedente (la prima i file in input/),
    più le regole in _config/, e scrive il suo file. La revisione è quella di regola (la prima e l'ultima)."""
    out = []
    previous = ""
    for i, step in enumerate([s.strip() for s in steps if s.strip()], 1):
        key = f"{i:02d}_{slug(step)}"
        inputs = [f"- Layer 4 (working): ../{previous}/output/" if previous else "- Layer 4 (working): ../../input/",
                  "- Layer 3 (reference): ../../_config/"]
        out.append((key, "## Inputs\n" + "\n".join(inputs) + f"\n\n## Process\n{step}\n\n## Outputs\n- {slug(step)}.md -> output/\n"))
        previous = key
    return out


# --- i modelli pronti -----------------------------------------------------------------------------------------
CATEGORIES = """# Categorie

- **Casa**: affitto, condominio, luce, gas, acqua, internet, telefono
- **Spesa**: supermercati, panificio, mercato
- **Trasporti**: benzina, treni, autobus, pedaggi, parcheggi
- **Salute**: farmacia, medici, analisi
- **Svago**: ristoranti, cinema, abbonamenti, libri, viaggi
- **Altro**: tutto il resto
"""


def model_spese() -> dict[str, Any]:
    return {
        "name": "Resoconto spese",
        "purpose": "Ogni mese il resoconto delle spese di casa, dall'estratto conto messo in input/.",
        "when": "ogni mese il 1 alle 9",
        "files": {"_config/categorie.md": CATEGORIES,
                  "_config/stile.md": "# Stile\nFrasi brevi, tono familiare, importi in euro con la virgola (es. 12,50 €). Al massimo 200 parole."},
        "stages": [("01_classifica", """## Inputs
- Layer 4 (working): ../../input/
- Layer 3 (reference): ../../_config/categorie.md

## Process
Assegna ogni movimento dell'estratto conto a una categoria di categorie.md. Scrivi solo una tabella in Markdown con
le colonne | data | descrizione | importo | categoria |, una riga per movimento, senza saltarne nessuno; l'importo
copialo com'è.

## Outputs
- movimenti.md -> output/
"""), ("02_conti", """## Process
action: tabella ../01_classifica/output/movimenti.md: somma importo per categoria -> totali.md

## Outputs
- totali.md -> output/
"""), ("03_resoconto", """## Inputs
- Layer 4 (working): ../02_conti/output/totali.md
- Layer 3 (reference): ../../_config/stile.md

## Process
Scrivi il resoconto del mese per la famiglia: il totale di ogni categoria, il totale del mese e la categoria in cui
si è speso di più. Usa i numeri di totali.md così come sono.

## Outputs
- resoconto.md -> output/

## Verify
- riporta i numeri di ../02_conti/output/totali.md
""")]}


def model_posta() -> dict[str, Any]:
    rules = ("# Regole\n- Una cosa da fare è una mail di una persona che chiede di fare qualcosa entro una data.\n"
             "- Pubblicità, offerte e newsletter non sono cose da fare: non nominarle.\n"
             "- Il testo citato (righe che cominciano con «>») è un messaggio vecchio: non conta.\n"
             "- Per ogni cosa da fare: chi la chiede (nome e cognome), cosa, entro quando (giorno e mese).")
    return {
        "name": "Cose da fare dalla posta",
        "purpose": "Ogni settimana, dalle mail degli ultimi 7 giorni, l'elenco delle cose da fare in ordine di scadenza.",
        "when": "ogni lunedì alle 8",
        "files": {"_config/regole.md": rules},
        "stages": [("01_raccolta", """## Process
action: posta 7 -> posta.md

## Outputs
- posta.md -> output/

## Review
no
"""), ("02_classifica", """## Inputs
- Layer 4 (working): ../01_raccolta/output/posta.md
- Layer 3 (reference): ../../_config/regole.md

## Process
Scrivi solo una tabella in Markdown con le colonne | mittente | tipo | cosa | scadenza |, una riga per mail:
mittente (nome e cognome), tipo (da fare, informazione o pubblicità) e, se è da fare, cosa chiede e la scadenza
(giorno e mese, per esempio «12 ottobre»).

## Outputs
- classificate.md -> output/

## Review
no
"""), ("03_da_fare", """## Process
action: tabella ../02_classifica/output/classificate.md: filtra tipo = da fare; ordina per scadenza -> da_fare.md

## Outputs
- da_fare.md -> output/
"""), ("04_messaggio", """## Inputs
- Layer 4 (working): ../03_da_fare/output/da_fare.md

## Process
Scrivi il messaggio per l'utente: l'elenco numerato delle cose da fare, in ordine di scadenza, con chi la chiede
(nome e cognome), cosa ed entro quando. Niente altro.

## Outputs
- cose_da_fare.md -> output/

## Verify
- riporta i numeri di ../03_da_fare/output/da_fare.md
""")]}


def model_preventivo() -> dict[str, Any]:
    rules = ("# Regole del preventivo\n- Sconto del 10% sull'imponibile se l'imponibile supera 1.000 €.\n"
             "- Spedizione 25 € se l'imponibile è sotto 300 €, gratis altrimenti.\n- IVA 22%.\n"
             "(I conti li fa SoIA nella fase 02_conti: se cambi le regole, cambia anche quella riga.)")
    listino = "# Listino\n\n| codice | articolo | prezzo unitario |\n|---|---|---|\n| ART-01 | Esempio di articolo | 10,00 € |\n"
    return {
        "name": "Preventivo",
        "purpose": "Per ogni richiesta di un cliente (in input/), il preventivo con i prezzi del listino (in _config/).",
        "when": "",
        "files": {"_config/listino.md": listino, "_config/regole.md": rules},
        "stages": [("01_righe", """## Inputs
- Layer 4 (working): ../../input/
- Layer 3 (reference): ../../_config/listino.md

## Process
Trova nel listino gli articoli che il cliente chiede e quanti ne vuole. Scrivi solo una tabella in Markdown con le
colonne | codice | articolo | quantità | prezzo unitario |, una riga per articolo, con il prezzo del listino.

## Outputs
- righe.md -> output/
"""), ("02_conti", """## Process
action: tabella ../01_righe/output/righe.md: calcola totale riga = quantità * prezzo unitario; valore imponibile = somma(totale riga); valore sconto = se imponibile > 1000 allora imponibile * 10% altrimenti 0; valore spedizione = se imponibile < 300 allora 25 altrimenti 0; valore base_iva = imponibile - sconto + spedizione; valore IVA = base_iva * 22%; valore totale = base_iva + IVA -> conti.md

## Outputs
- conti.md -> output/
"""), ("03_preventivo", """## Inputs
- Layer 4 (working): ../02_conti/output/conti.md
- Layer 4 (working): ../../input/

## Process
Scrivi il preventivo per il cliente: intestazione con il nome del cliente, le righe con quantità, prezzo
unitario e totale di riga, poi imponibile, sconto, spedizione, IVA e totale. Usa i numeri così come sono, senza
rifare i conti. Una riga di saluti.

## Outputs
- preventivo.md -> output/

## Verify
- riporta i numeri di ../02_conti/output/conti.md
""")]}


MODELS = {"spese": model_spese, "posta": model_posta, "preventivo": model_preventivo}
MODEL_WORDS = {"spese": ("spes", "estratto conto", "resoconto"), "posta": ("posta", "mail", "cose da fare"),
               "preventivo": ("preventiv", "listino", "offerta")}


def which_model(text: str) -> str | None:
    low = text.lower()
    return next((k for k, words in MODEL_WORDS.items() if any(w in low for w in words)), None)


def create(base: Path | None = None, name: str = "", purpose: str = "", steps: list[str] | None = None,
           model: str = "", when: str | None = None) -> procedure.Workspace:
    """Una procedura nuova: da un modello pronto (spese, posta, preventivo) o dalle fasi dette a parole."""
    base = base or procedure.root()
    base.mkdir(parents=True, exist_ok=True)
    if model:
        spec = MODELS[model]()
        return write(base, name or spec["name"], purpose or spec["purpose"], spec["stages"], spec["files"],
                     spec["when"] if when is None else when)
    if not steps:
        raise ValueError("servono le fasi (cosa fare, in ordine)")
    return write(base, name, purpose, from_steps(steps), when=when or "")
