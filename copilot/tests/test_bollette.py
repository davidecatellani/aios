import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

from aios_copilot import bollette
from aios_copilot.fileindex import FileIndex
from aios_copilot.tools.documents import DocumentsRouter, make_tools

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addestramento"))
import bollette_prova as bp  # noqa: E402

NOW = bp.NOW


class Reader:
    """Un modello finto che legge le bollette inventate come dovrebbe (cerca le righe giuste)."""

    def __init__(self, lie=False):
        self.calls, self.lie = [], lie

    def chat(self, messages, tools):
        self.calls.append({"prompt": 1, "risposta": 1})
        text = messages[-1]["content"]
        if "Ecco i miei documenti" in text:
            return {"content": "Non lo so."}
        first = text.splitlines()[0]
        total = re.search(r"(?:Totale da pagare|Totale fattura \(IVA inclusa\)|Importo da pagare|Rata 2 di 2): ([\d.,]+)", text)
        due = re.search(r"(?:Scadenza|entro il|Scadenza pagamento|con scadenza)[: ]+(\d{2}/\d{2}/\d{4}|\d+ \w+ \d{4})", text)
        kind = "avviso di pagamento" if "AVVISO" in text else "fattura" if "Fattura" in text else "bolletta" if total else "altro"
        service = next((s for w, s in (("TIM", "telefono"), ("Vodafone", "telefono"), ("Fastweb", "internet")) if w in first), "")
        return {"content": json.dumps({"tipo": kind, "servizio": service, "emittente": first.replace(" S.p.A.", ""),
                                       "numero": "1", "data": "", "scadenza": due.group(1) if due else "",
                                       "totale": "999,99" if self.lie else (total.group(1) if total else "")})}


def indexed(tmp_path, files):
    folder = tmp_path / "Documenti"
    folder.mkdir()
    for name, text in files.items():
        (folder / name).write_text(text)
    index = FileIndex(db_path=tmp_path / "indice.db", roots=[folder], excluded=[])
    while index.has_work():
        index.step(index.clock() + 5)
    return index


def test_silver_gold_on_invented_bills(tmp_path):
    files, bills = bp.make_folder(random.Random("bollette-0"))
    exp = bp.expected(bills)
    index = indexed(tmp_path, files)
    facts = bollette.Facts(tmp_path / "documenti.db")
    model = Reader()
    assert facts.update(index, model) == len(files)
    assert len(model.calls) == len(bills) + 1  # appunti, ricetta, lettera…: scartati dal codice; il verbale lo legge il modello
    assert facts.update(index, model) == 0 and facts.unread(index) == 0
    pay = bollette.to_pay_text(facts, NOW, bp.DAYS)
    assert all(ok for _, ok in bp.check_to_pay(pay, exp)), pay
    assert "Con addebito automatico (non devi fare niente)" in pay
    luce = bollette.spent_text(facts, "luce", NOW.year, NOW)
    assert all(ok for _, ok in bp.check_luce(luce, exp)), luce
    assert bollette.spent_text(facts, "bollette", NOW.year, NOW).startswith("Bollette e fatture nel 2026:")
    rows = dict(facts.db.execute("SELECT path, servizio FROM documenti_fatti WHERE tipo != 'altro'").fetchall())
    assert {v for v in rows.values()} >= {"luce", "gas", "telefono", "internet", "acqua", "rifiuti"}  # dal testo, senza modello


def test_the_code_checks_the_numbers(tmp_path):
    files, bills = bp.make_folder(random.Random("bollette-1"))
    index = indexed(tmp_path, files)
    facts = bollette.Facts(tmp_path / "documenti.db")
    facts.update(index, Reader(lie=True))  # il modello inventa sempre 999,99
    got = dict(facts.db.execute("SELECT path, totale FROM documenti_fatti WHERE tipo != 'altro'").fetchall())
    for b in bills:
        path = str((tmp_path / "Documenti" / b["file"]).resolve())
        if b["servizio"] != "rifiuti":  # la TARI non ha «totale da pagare»: lì l'importo resta da controllare
            assert got[path] == b["totale"], b
    problems = [r[0] for r in facts.db.execute("SELECT controllo FROM documenti_fatti WHERE controllo != ''")]
    assert problems and all("999,99 non è nel documento" in p for p in problems)


def test_dates_and_debit():
    text = "Fattura del 3 ottobre 2026. Da pagare entro il 28/10/2026 con addebito diretto SEPA (SDD)."
    known = bollette.dates_in(text)
    assert [d.isoformat() for d in known] == ["2026-10-03", "2026-10-28"]
    assert bollette.to_date("28 ottobre", known).isoformat() == "2026-10-28"
    assert bollette.to_date("30/10/2026", known) is None  # non è nel documento
    assert bollette.DIRECT_DEBIT.search(text) and not bollette.DIRECT_DEBIT.search("bollettino postale")
    assert bollette.code_only("Cara nonna, ti scrivo") and not bollette.code_only("Bolletta: totale 12,00 €")


def test_nova_tools_and_task(tmp_path, monkeypatch):
    files, _ = bp.make_folder(random.Random("bollette-0"))
    index = indexed(tmp_path, files)
    monkeypatch.setattr(bollette, "db_path", lambda: tmp_path / "documenti.db")
    task = bollette.BillsTask(index, model=lambda: Reader())
    assert task.available() and task.has_work()
    while task.facts.pending(index):
        task.step(0.5)
    assert not task.facts.unread(index)
    tools = {t.name: t for t in make_tools(lambda: index, now=lambda: NOW)}
    assert tools["bills_to_pay"].func().startswith("Da pagare nei prossimi 30 giorni:")
    assert tools["bills_spent"].func(servizio="luce", anno="2026").startswith("Luce nel 2026:")
    r = DocumentsRouter()
    assert r.match("che bollette devo pagare?").tool == "bills_to_pay"
    assert r.match("quanto ho speso di luce nel 2025").args == {"servizio": "luce", "anno": "2025"}
    assert r.match("quanto ho speso di AI?") is None

    class Off:
        def chat(self, messages, tools):
            raise OSError("spento")

    other = tmp_path / "altro"
    other.mkdir()
    off = bollette.BillsTask(indexed(other, {"b.txt": "Bolletta: totale da pagare 12,00 €"}), bollette.Facts(other / "d.db"), model=Off)
    off.step(0.5)
    assert not off.available()  # modello spento: si riprova più tardi
