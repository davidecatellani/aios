import json
import shutil
import time
from datetime import date, datetime
from pathlib import Path

import pytest

from aios_copilot import documents as docs
from aios_copilot.agent import Agent
from aios_copilot.fileindex import FileIndex
from aios_copilot.mesh.delegate import Assistant
from aios_copilot.mesh.files import FileShare, PhoneServer
from aios_copilot.tools import documents as doc_tools
from aios_copilot.tools.base import Runner

DIET = """DIETA SETTIMANALE - Dott.ssa Bianchi
Lunedì
Colazione: 1 yogurt greco, 30 g fiocchi d'avena e frutti di bosco
Pranzo: 80 g pasta integrale con zucchine, insalata
Cena: 150 g salmone, verdure grigliate, 1 fetta di pane integrale
Martedì
Colazione: latte parzialmente scremato, 3 fette biscottate con marmellata
Pranzo: 70 g riso basmati, petto di pollo, spinaci
Spuntino: 1 mela
Cena: 2 uova, insalata mista e pomodori
Mercoledi
Pranzo: 80 g farro, ceci, carote
Cena: merluzzo al forno, patate
"""


def pdf(text: str) -> bytes:
    """Un PDF minimo ma valido, con il testo riga per riga."""
    lines = [l.replace("(", "").replace(")", "") for l in text.splitlines()]
    stream = "BT /F1 11 Tf 50 800 Td 14 TL " + " ".join(f"({l}) '" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    h = tmp_path / "home"
    d = h / "Documents"
    d.mkdir(parents=True)
    files = {
        "Bolletta luce giugno 2026.pdf": "Enel Energia - bolletta luce\nPeriodo: 01/06/2026 - 30/06/2026\nTotale da pagare 61,20 euro",
        "Bolletta luce luglio 2026.pdf": "Enel Energia - bolletta luce\nPeriodo: 01/07/2026 - 31/07/2026\nTotale da pagare 74,90 euro",
        "Bolletta gas luglio 2026.pdf": "Italgas - bolletta gas\nPeriodo luglio 2026\nTotale 22,10 euro",
        "Bolletta luce 2025-07.pdf": "Enel Energia - bolletta luce\nPeriodo: 01/07/2025 - 31/07/2025\nTotale 70,00 euro",
        "Dieta Bianchi.pdf": DIET,
    }
    for name, text in files.items():
        (d / name).write_bytes(pdf(text))
    index = FileIndex(tmp_path / "index.db", roots=[h], excluded=[])
    while index.has_work():
        index.step(time.time() + 5)
    return h, index


pytestmark = pytest.mark.skipif(not shutil.which("pdftotext"), reason="serve pdftotext")


def test_finds_the_right_bill(home):
    _, index = home
    found = docs.find_documents(index, "bolletta di luglio della luce", date(2026, 10, 3))
    assert found[0][0].name == "Bolletta luce luglio 2026.pdf"
    assert docs.find_documents(index, "bolletta della luce di luglio 2025", date(2026, 10, 3))[0][0].name == "Bolletta luce 2025-07.pdf"
    assert docs.find_documents(index, "bolletta del gas di luglio", date(2026, 10, 3))[0][0].name == "Bolletta gas luglio 2026.pdf"


def test_diet_answers_without_the_ai_model(home):
    _, index = home

    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM")

    monday = datetime(2026, 10, 5, 8, 0)
    tools = doc_tools.make_tools(lambda: index, Runner(), now=lambda: monday)
    agent = Agent(NoModel(), tools, confirm=lambda *a, **k: True, routers=[doc_tools.DocumentsRouter()])
    out = agent.ask("Cosa devo mangiare oggi?")
    assert out.startswith("Secondo la tua dieta («Dieta Bianchi.pdf»), lunedì:") and "salmone" in out and "riso" not in out
    out = agent.ask("cosa devo mangiare domani a pranzo")
    assert "martedì a pranzo" in out and "riso basmati" in out and "uova" not in out
    assert "farro" in agent.ask("cosa prevede la dieta per mercoledì")  # «Mercoledi» senza accento nel PDF
    out = agent.ask("fammi la lista della spesa")
    assert "☐ fette biscottate" in out and "☐ salmone" in out and "🥩 Carne e pesce" in out
    saved = Path.home() / "Documents/Lista della spesa.md"
    assert "☐ riso basmati" in saved.read_text()


def test_from_the_phone_the_file_arrives_as_a_button(home, tmp_path):
    h, index = home
    server = PhoneServer(FileShare(h))

    def phone_agent(confirm):
        class NoModel:
            def chat(self, *a):
                raise AssertionError("niente LLM")

        tools = doc_tools.make_tools(lambda: index, Runner(), now=lambda: datetime(2026, 10, 3, 9))
        return Agent(NoModel(), tools, confirm, routers=[doc_tools.DocumentsRouter()])

    assistant = Assistant(phone_agent, attach=server.attach)
    job_id = assistant.ask("fammi vedere la bolletta di luglio della luce")
    job = assistant.jobs[job_id]
    for _ in range(100):
        if job.answer:
            break
        time.sleep(0.02)
    assert job.answer.startswith("Ecco «Bolletta luce luglio 2026.pdf»")
    files = [e for e in job.events if e["kind"] == "file"]
    assert files[0]["name"] == "Bolletta luce luglio 2026.pdf" and files[0]["url"].startswith("/scarica/")
    assert server.redeem(files[0]["url"].rsplit("/", 1)[1]).name == "Bolletta luce luglio 2026.pdf"
    assert server.attach(tmp_path / "fuori.pdf") is None  # niente file fuori dalla cartella personale


def test_month_mentions():
    assert docs.month_mentions("Periodo: 01/07/2026 - 31/07/2026") == {7}
    assert docs.month_mentions("bolletta lug 2026") == {7}
    assert docs.month_mentions("mare e sole") == set()
    assert docs.hints("la bolletta del mese scorso", date(2026, 1, 10))[0] == {12}
