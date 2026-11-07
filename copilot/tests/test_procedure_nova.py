import json
import time
from datetime import datetime
from types import SimpleNamespace

from aios_copilot import procedure, procedure_crea
from aios_copilot.tools import procedure as ptools


class Model:
    def __init__(self, reply):
        self.reply, self.seen = reply, []

    def chat(self, messages, tools):
        self.seen.append(messages)
        return {"content": self.reply(messages)}


def test_templates_and_steps(tmp_path):
    spese = procedure_crea.create(tmp_path, model="spese")
    assert spese.name == "Resoconto spese" and [s.key for s in spese.stages()] == ["01_classifica", "02_conti", "03_resoconto"]
    assert (spese.path / "_config/categorie.md").is_file() and (spese.path / "input/LEGGIMI.txt").is_file()
    plan = procedure.schedule(spese)
    assert plan["ogni"] == "mese" and plan["giorno"] == 1 and abs(plan["ultimo"] - time.time()) < 5
    assert procedure.due(tmp_path) == []  # creata adesso: non parte subito «per il 1»
    assert procedure.due(tmp_path, now=datetime(2099, 1, 2))[0].name == "Resoconto spese"
    posta = procedure_crea.create(tmp_path, model="posta")
    assert posta.stages()[0].contract.actions[0].tool == "posta" and procedure.describe_when(procedure.schedule(posta)) == "ogni lunedì alle 8:00"
    mine = procedure_crea.create(tmp_path, name="Rassegna", purpose="Rassegna stampa",
                                 steps=["Riassumi gli articoli", "Scegli i tre più importanti", "Scrivi la mail"])
    stages = mine.stages()
    assert [s.key for s in stages] == ["01_riassumi_gli_articoli", "02_scegli_i_tre", "03_scrivi_la_mail"]
    assert stages[1].contract.inputs[0].path == stages[0].output_dir and stages[0].contract.inputs[0].path.name == "input"
    assert procedure.schedule(mine) is None and mine.review_of(stages[1], stages) is False
    assert procedure_crea.which_model("ogni mese voglio il resoconto delle spese") == "spese"


def test_nova_creates_runs_and_continues(tmp_path):
    notes = []
    model = Model(lambda m: ("| codice | articolo | quantità | prezzo unitario |\n|---|---|---|---|\n| SCR-01 | Scrivania | 2 | 289,00 € |"
                             if "righe.md" in m[-1]["content"] else
                             "Preventivo per Officina Bertoni: SCR-01 2 × 289,00 = 578,00. Imponibile 578,00, sconto 0,00, "
                             "spedizione 0,00, base iva 578,00, IVA 127,16, totale 705,16"))
    tools = {t.name: t for t in ptools.make_tools(model=lambda: model, base=lambda: tmp_path,
                                                   notify=lambda title, text: notes.append(text), background=False)}
    said = tools["create_procedure"].func(cosa="i preventivi per i clienti")
    assert "Ho creato la procedura «Preventivo»" in said and "listino" in said
    ws = procedure.find("preventivo", tmp_path)
    (ws.path / "_config/listino.md").write_text("| codice | articolo | prezzo unitario |\n|---|---|---|\n| SCR-01 | Scrivania | 289,00 € |\n")
    (ws.path / "input/richiesta.md").write_text("Officina Bertoni: ci servono due scrivanie.")
    out = tools["run_procedure"].func(nome="il preventivo")
    assert "ferma alla fase «righe», da guardare" in out and notes  # la prima fase si guarda
    assert "da guardare" in tools["procedure_status"].func(nome="preventivo")
    out = tools["continue_procedure"].func(nome="")  # «vai avanti»: c'è una sola procedura che aspetta
    assert "ferma alla fase «preventivo», da guardare" in out
    conti = (ws.path / "stages/02_conti/output/conti.md").read_text()
    assert "Totale: 705,16" in conti and "Imponibile: 578,00" in conti  # i conti li fa il codice
    assert "finita" in tools["continue_procedure"].func(nome="preventivo")
    assert "Fatto: «Preventivo» parte ogni giorno alle 18:00" in tools["schedule_procedure"].func(nome="preventivo", quando="ogni giorno alle 18")
    assert "solo quando lo chiedi" in tools["schedule_procedure"].func(nome="preventivo", quando="solo a mano")
    assert "Non trovo una procedura «bollette»" in tools["run_procedure"].func(nome="bollette")
    assert "le fasi sono" in tools["redo_procedure"].func(nome="preventivo", fase="spedizione").lower()


def test_router(tmp_path):
    procedure_crea.create(tmp_path, model="spese")
    procedure_crea.create(tmp_path, model="posta")
    r = ptools.ProceduresRouter(base=lambda: tmp_path)
    assert r.match("fai il resoconto spese").tool == "run_procedure"
    assert r.match("avvia la procedura delle spese").args == {"nome": "delle spese"}
    assert r.match("avvia spotify") is None and r.match("avvia la posta") is None  # restano alle app e alla posta
    assert r.match("fai le cose da fare dalla posta").tool == "run_procedure"
    assert r.match("a che punto è il resoconto?").tool == "procedure_status"
    assert r.match("le mie procedure").tool == "list_procedures"
    assert r.match("rifai i conti del resoconto spese").args == {"nome": "resoconto spese", "fase": "i conti"}
    assert r.match("vai avanti") is None  # nessuna procedura aspetta: «vai avanti» non è per le procedure
    ws = procedure.find("spese", tmp_path)
    ws.save_state({"stages": {"01_classifica": {"status": procedure.REVIEW}}})
    assert r.match("vai avanti").tool == "continue_procedure"


def test_recent_mail_and_one_scheduler(tmp_path):
    mails = [SimpleNamespace(sender_name="Paolo Bianchi", sender="paolo@x.it", subject="Report", body="Entro il 12 ottobre",
                             snippet="", date=datetime(2026, 10, 5)),
             SimpleNamespace(sender_name="Zalando", sender="z@z.it", subject="Saldi", body="-40%", snippet="", date=datetime(2026, 9, 1))]
    store = SimpleNamespace(inbox=lambda limit: mails)
    text = procedure.recent_mail(7, store=store, now=datetime(2026, 10, 7))
    assert "Paolo Bianchi" in text and "Zalando" not in text and "Data: 05/10/2026" in text
    assert "Nessuna mail" in procedure.recent_mail(1, store=store, now=datetime(2026, 10, 7))
    lock = tmp_path / "lucchetto"
    assert ptools._only_one(lock) and not ptools._only_one(lock)  # un solo Nova fa partire le procedure
    plan = procedure.parse_when("ogni lunedì alle 8:30")
    assert procedure.last_due(plan, datetime(2026, 11, 7, 1, 0)) == datetime(2026, 11, 2, 8, 30)
    assert json.loads(json.dumps(plan))["ogni"] == "settimana"


def _letter(tmp_path, name):
    ws = procedure_crea.create(tmp_path, name=name, purpose="La lettera ai soci", steps=["Scrivi la lettera ai soci"])
    return ws, ws.stages()[0]


def test_corrections_become_instructions(tmp_path):
    from aios_copilot.tools import procedure as ptools

    ws, st = _letter(tmp_path, "Lettera")
    notes = []
    model = Model(lambda m: "Gentile socio,\nla riunione è giovedì.\nSaluti")
    r = procedure.Runner(ws, model, notify=lambda t, m: notes.append(m), wait=lambda: True)
    out = st.output_files()[0]
    for giro in range(3):
        if giro:
            r.redo(st.key)
        assert r.run().status == "da guardare"
        out.write_text(out.read_text().replace("Gentile socio", "Ciao socio") + f"(giro {giro})\n")  # la correzione di sempre
        r.proceed()
    assert len(procedure.corrections(ws, st.key)) == 3
    assert procedure.proposals(ws)[st.key]["regola"] == "scrivi «Ciao» invece di «Gentile»"  # trovata dal codice
    assert any("correggi sempre la stessa cosa" in n for n in notes)
    assert "proposta per le istruzioni" in ptools._status_line(ws, r)
    router = ptools.ProceduresRouter(base=lambda: tmp_path)
    assert router.match("sì, scrivila nelle istruzioni").tool == "procedure_rule"
    tools = {t.name: t for t in ptools.make_tools(base=lambda: tmp_path, background=False)}
    said = tools["procedure_rule"].func(nome="", decisione="sì")
    assert "Dal prossimo giro la fa da sola" in said
    contract = (st.dir / "CONTEXT.md").read_text()
    assert "Regola imparata dalle tue correzioni: scrivi «Ciao» invece di «Gentile»." in contract
    assert procedure.parse_contract(contract, st.dir, ws.path).process.endswith("invece di «Gentile».")  # dentro Process
    assert "non ha proposte" in tools["procedure_rule"].func(nome="lettera", decisione="sì")


def test_model_finds_the_rule_when_corrections_differ(tmp_path):
    ws, st = _letter(tmp_path, "Verbale")
    asked = []

    def reply(m):
        if "correzioni" in m[0]["content"]:
            asked.append(m)
            return '{"regola": "tieni l\'apertura in una frase sola"}'
        return "Apertura lunga.\nAltra frase lunga.\nCorpo."

    r = procedure.Runner(ws, Model(reply), wait=lambda: True)
    out = st.output_files()[0]
    for giro in range(3):
        if giro:
            r.redo(st.key)
        r.run()
        out.write_text(f"Apertura breve {giro}.\nCorpo.\n")
        r.proceed()
    assert asked and procedure.proposals(ws)[st.key]["regola"] == "tieni l'apertura in una frase sola"
    assert "lascio le istruzioni" in procedure.decide(ws, st.key, False)
    assert "Regola imparata" not in (st.dir / "CONTEXT.md").read_text()
