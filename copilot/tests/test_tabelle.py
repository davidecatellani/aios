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
