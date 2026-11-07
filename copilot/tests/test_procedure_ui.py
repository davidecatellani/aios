from types import SimpleNamespace

from aios_copilot import procedure
from aios_copilot.shell.apps import register_procedures
from aios_copilot.tools import procedure as ptools


class App:
    def __init__(self, agent):
        self.agent, self.routes = agent, {}

    def route(self, method, pattern, handler):
        self.routes[(method, pattern)] = handler

    def get(self, **q):
        return self.routes[("GET", r"/api/procedure")](None, {}, {k: str(v) for k, v in q.items()})

    def post(self, **b):
        return self.routes[("POST", r"/api/procedure")](None, b, {})


class Model:
    def chat(self, messages, tools):
        if "righe.md" in messages[-1]["content"]:
            return {"content": "| codice | articolo | quantità | prezzo unitario |\n|---|---|---|---|\n| SCR-01 | Scrivania | 2 | 289,00 € |"}
        return {"content": "Preventivo: SCR-01 2 × 289,00 = 578,00. Imponibile 578,00, sconto 0,00, spedizione 0,00, "
                           "base iva 578,00, IVA 127,16, totale 705,16"}


def test_settings_and_files_view(tmp_path, monkeypatch):
    monkeypatch.setenv("AIOS_CASA", str(tmp_path))
    monkeypatch.setattr(ptools, "_default_notify", lambda title, text: None)
    app = App(SimpleNamespace(model=Model(), tools={}))
    register_procedures(app)
    code, d = app.get()
    assert code == 200 and d["procedure"] == [] and {m["id"] for m in d["modelli"]} == {"spese", "posta", "preventivo"}
    code, r = app.post(azione="crea", modello="preventivo")
    assert code == 200 and "Ho creato la procedura «Preventivo»" in r["messaggio"]
    ws = procedure.find("preventivo", tmp_path / "Procedure")
    (ws.path / "_config/listino.md").write_text("| codice | articolo | prezzo unitario |\n|---|---|---|\n| SCR-01 | Scrivania | 289,00 € |\n")
    (ws.path / "input/richiesta.md").write_text("Officina Bertoni: due scrivanie.")
    p = app.get()[1]["procedure"][0]
    assert p["nome"] == "Preventivo" and p["cartella"] == "Procedure/Preventivo" and p["input"] == "Procedure/Preventivo/input"
    assert [f["stato"] for f in p["fasi"]] == ["da fare"] * 3 and p["fasi"][0]["istruzioni"] == "Procedure/Preventivo/stages/01_righe/CONTEXT.md"
    assert app.post(nome="Preventivo", azione="avvia")[1]["ok"]
    ptools._running["Preventivo"].join(10)  # in sottofondo, come davvero
    p = app.get(p="Procedure/Preventivo/stages")[1]["procedura"]  # dall'app File, anche da una sottocartella
    assert p["fasi"][0]["stato"] == "da guardare" and p["fasi"][0]["risultati"] == ["Procedure/Preventivo/stages/01_righe/output/righe.md"]
    app.post(nome="Preventivo", azione="avanti")
    ptools._running["Preventivo"].join(10)
    p = app.get()[1]["procedure"][0]
    assert [f["stato"] for f in p["fasi"]] == ["fatta", "fatta", "da guardare"]
    assert "ogni lunedì alle 8:00" in app.post(nome="Preventivo", azione="quando", quando="ogni lunedì alle 8")[1]["messaggio"]
    assert app.get(p="Documenti")[1] == {"procedura": None}
    assert app.post(nome="Bollette", azione="avvia")[0] == 404
