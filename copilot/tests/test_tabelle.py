from pathlib import Path

import pytest

from aios_copilot import procedure, tabelle


def test_numbers():
    assert tabelle.parse_number("1.234,50 €") == 1234.5 and tabelle.parse_number("-12,30") == -12.3
    assert tabelle.parse_number("1234.5") == 1234.5 and tabelle.parse_number("1.000") == 1000 and tabelle.parse_number("SCR-01") is None
    assert tabelle.numbers_in("Totale 1.234,50 € e 12,5 poi 7") == [1234.5, 12.5, 7.0]
    assert tabelle.fmt(1234.5) == "1.234,50"


def test_table_compute_and_sums(tmp_path):
    (tmp_path / "righe.md").write_text("Ecco le righe:\n\n| Codice | Articolo | Quantità | Prezzo unitario |\n|---|---|---:|---:|\n"
                                       "| SCR-02 | Scrivania | 2 | 549,00 € |\n| LAM-01 | Lampada | 3 | 34,90 € |\n")
    out = tabelle.run("righe.md: calcola totale = quantità × prezzo; somma totale", tmp_path, tmp_path)
    assert "| SCR-02 | Scrivania | 2 | 549,00 € | 1.098,00 |" in out and "Somma di totale: 1.202,70" in out
    (tmp_path / "mov.csv").write_text("data;descrizione;importo;categoria\n01/10;Esselunga;-45,20;Spesa\n02/10;Enel;-61,20;Casa\n"
                                      "03/10;Coop;-14,80;Spesa\n")
    out = tabelle.run("mov.csv: somma importo per categoria", tmp_path, tmp_path)
    assert "| Spesa | -60,00 |" in out and "| Casa | -61,20 |" in out and "| Totale | -121,20 |" in out
    with pytest.raises(ValueError, match="colonna «sconto»"):
        tabelle.run("mov.csv: somma sconto", tmp_path, tmp_path)
    with pytest.raises(ValueError, match="cartella della procedura"):
        tabelle.run("../../etc/passwd: somma a", tmp_path / "x", tmp_path / "x")


class Model:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def chat(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        reply = self.replies.pop(0)
        return reply(messages) if callable(reply) else reply


def workspace(tmp_path: Path) -> procedure.Workspace:
    ws = tmp_path / "P"
    (ws / "input" / "posta").mkdir(parents=True)
    for i, text in enumerate(["Paolo chiede il report entro il 12", "Offerte Zalando", "Anna: firma entro il 3"], 1):
        (ws / "input" / "posta" / f"{i:02d}.md").write_text(text)
    stages = {
        "01_righe": "## Inputs\n- ../../input/righe.md\n## Process\nCopia le righe.\n## Outputs\n- righe.md\n## Review\nno\n",
        "02_conti": ("## Process\naction: tabella ../01_righe/output/righe.md: calcola totale = quantità * prezzo; somma totale -> righe_totali.md\n"
                     "## Outputs\n- righe_totali.md\n## Review\nno\n"),
        "03_testo": ("## Inputs\n- ../02_conti/output/righe_totali.md\n## Process\nScrivi il preventivo.\n## Outputs\n- preventivo.md\n"
                     "## Verify\n- riporta i numeri di ../02_conti/output/righe_totali.md\n## Review\nno\n"),
        "04_posta": "## For each\n../../input/posta/\n## Process\nDa fare o no?\n## Outputs\n- posta.md\n## Review\nno\n",
    }
    (ws / "input" / "righe.md").write_text("x")
    for key, text in stages.items():
        (ws / "stages" / key).mkdir(parents=True)
        (ws / "stages" / key / "CONTEXT.md").write_text(text)
    return procedure.Workspace(ws)


def test_code_does_the_sums_and_checks_catch_invented_numbers(tmp_path):
    ws = workspace(tmp_path)
    s2 = ws.stages()[1]
    assert s2.contract.actions[0].output == "righe_totali.md" and s2.contract.outputs == ["righe_totali.md"]
    assert ws.stages()[3].contract.each.name == "posta"
    model = Model([
        {"content": "| codice | quantità | prezzo |\n|---|---|---|\n| SCR-02 | 2 | 549,00 |\n| LAM-01 | 3 | 34,90 |"},
        {"content": "Scrivania 2 × 450,00 = 900,00. Totale 1.000,00"},          # prezzi inventati: il controllo li prende
        {"content": "SCR-02 2 × 549,00 = 1.098,00; LAM-01 3 × 34,90 = 104,70. Totale 1.202,70"},
        lambda m: {"content": "da fare" if ("Paolo" in m[1]["content"] or "Anna" in m[1]["content"]) else "pubblicità"},
        lambda m: {"content": "pubblicità"},
        lambda m: {"content": "da fare"},
    ])
    report = procedure.Runner(ws, model, wait=lambda: True).run()
    assert report.status == "finita", report.message
    totals = (ws.path / "stages/02_conti/output/righe_totali.md").read_text()
    assert "1.098,00" in totals and "Somma di totale: 1.202,70" in totals  # fatti dal codice, senza modello
    retry = model.seen[2][1]["content"]
    assert "Da correggere" in retry and "549" in retry  # il controllo dice quali numeri mancano
    posta = (ws.path / "stages/04_posta/output/posta.md").read_text()
    assert posta.index("## 01.md") < posta.index("## 02.md") < posta.index("## 03.md") and "pubblicità" in posta
    one_mail = model.seen[3][1]["content"]
    assert "Paolo" in one_mail and "Zalando" not in one_mail and "Anna" not in one_mail  # una mail alla volta
    assert len(model.seen) == 6


def test_table_error_stops_the_stage(tmp_path):
    ws = workspace(tmp_path)
    model = Model([{"content": "niente tabella qui"}])
    report = procedure.Runner(ws, model, wait=lambda: True).run()
    assert report.status == "ferma" and report.stage == "02_conti" and "tabella non torna" in report.message


def test_filter_sort_dates_and_values(tmp_path):
    (tmp_path / "p.md").write_text("| mittente | tipo | cosa | scadenza |\n|---|---|---|---|\n"
                                   "| Paolo Bianchi | Da fare | report | entro il 24 febbraio |\n| Zalando | pubblicità | | |\n"
                                   "| Anna Neri | da fare | firma | 8 febbraio |\n| Elena Ricci | Da fare. | visita | 11/2 |\n")
    out = tabelle.run("p.md: filtra tipo = da fare; ordina per scadenza", tmp_path, tmp_path)
    assert "Zalando" not in out and out.index("Neri") < out.index("Ricci") < out.index("Bianchi")
    assert tabelle.parse_date("entro il 12 ottobre 2026") == (2026, 10, 12) and tabelle.parse_date("12/10") == (0, 10, 12)
    assert tabelle.parse_date("2026-10-12") == (2026, 10, 12) and tabelle.parse_date("domani") is None
    (tmp_path / "r.md").write_text("| codice | quantità | prezzo unitario |\n|---|---|---|\n| A | 2 | 549,00 € |\n| B | 3 | 34,90 |\n")
    rules = ("calcola totale riga = quantità * prezzo unitario; valore imponibile = somma(totale riga); "
             "valore sconto = se imponibile > 1000 allora imponibile * 10% altrimenti 0; "
             "valore spedizione = se imponibile < 300 allora 25 altrimenti 0; valore base_iva = imponibile - sconto + spedizione; "
             "valore iva = base_iva * 22%; valore totale = base_iva + iva")
    out = tabelle.run("r.md: " + rules, tmp_path, tmp_path)
    for line in ("Imponibile: 1.202,70", "Sconto: 120,27", "Spedizione: 0,00", "Iva: 238,13", "Totale: 1.320,56"):
        assert line in out
    with pytest.raises(ValueError, match="non è un valore calcolato prima"):
        tabelle.run("r.md: valore iva = base * 22%", tmp_path, tmp_path)


def test_for_each_rows_become_one_table():
    rows = [("01.md", "| mittente | tipo |\n|---|---|\n| Paolo | da fare |"), ("02.md", "| Mittente | Tipo |\n|---|---|\n| Zalando | pubblicità |")]
    merged = procedure._merge(rows)
    assert merged.count("| mittente | tipo |") == 1 and "| Zalando | pubblicità |" in merged and "##" not in merged
    assert "## 01.md" in procedure._merge([("01.md", "da fare"), ("02.md", "| a |\n|---|\n| b |")])
