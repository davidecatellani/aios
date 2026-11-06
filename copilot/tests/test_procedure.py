import json
from pathlib import Path

from aios_copilot import procedure
from aios_copilot.tools.base import Tool, params


def make(base: Path) -> procedure.Workspace:
    ws = base / "Procedure" / "Resoconto spese"
    (ws / "_config").mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("# Resoconto spese\nOgni mese il resoconto delle spese di casa.\n")
    (ws / "CONTEXT.md").write_text("01 raccolta → 02 conti → 03 resoconto\n")
    (ws / "_config" / "categorie.md").write_text("# Categorie\n\n## Casa\nluce, gas\n\n## Tono\nSemplice e breve.\n")
    stages = {
        "01_raccolta": """## Inputs
## Process
action: search_files bollette ottobre
## Outputs
- bollette.md -> output/
""",
        "02_conti": """## Inputs
- Layer 4 (working): ../01_raccolta/output/
- ../../_config/categorie.md#Casa

## Process
Somma le spese per categoria.

## Outputs
- conti.md -> output/

## Tools
- calculate

## Verify
- contiene: Totale
""",
        "03_resoconto": """## Inputs
- ../02_conti/output/conti.md
- Layer 3 (reference): ../../_config/categorie.md#Tono

## Process
Scrivi il resoconto per la famiglia.

## Outputs
- resoconto.md -> output/
- titolo.txt -> output/
""",
    }
    for key, text in stages.items():
        (ws / "stages" / key).mkdir(parents=True)
        (ws / "stages" / key / "CONTEXT.md").write_text(text)
    return procedure.Workspace(ws)


class Model:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def chat(self, messages, tools):
        self.seen.append(([dict(m) for m in messages], [t["function"]["name"] for t in tools]))
        return self.replies.pop(0)


def tools(calls):
    def search_files(query):
        calls.append(("search_files", query))
        return "Bolletta luce ottobre: 61,20 €\nBolletta gas ottobre: 38,80 €"

    def calculate(expression):
        calls.append(("calculate", expression))
        return "100.0"

    def send_mail(to):
        calls.append(("send_mail", to))
        return "mandata"

    return {"search_files": Tool("search_files", "Cerca i file", params(["query"], query="cosa"), search_files),
            "calculate": Tool("calculate", "Calcola", params(["expression"], expression="conto"), calculate),
            "send_mail": Tool("send_mail", "Manda una mail", params(["to"], to="a chi"), send_mail, requires_confirmation=True)}


def test_contract_format(tmp_path):
    ws = make(tmp_path)
    s1, s2, s3 = ws.stages()
    assert [s.key for s in ws.stages()] == ["01_raccolta", "02_conti", "03_resoconto"] and s2.name == "conti"
    assert s1.contract.actions == [procedure.Action("search_files", "bollette ottobre")] and s1.contract.process == ""
    assert [(i.path.name, i.section, i.layer) for i in s2.contract.inputs] == [("output", "", 4), ("categorie.md", "Casa", 3)]
    assert s2.contract.outputs == ["conti.md"] and s2.contract.tools == ["calculate"] and s2.contract.verify == ["contiene: Totale"]
    assert [i.layer for i in s3.contract.inputs] == [4, 3] and s3.contract.outputs == ["resoconto.md", "titolo.txt"]
    # revisione di regola: la prima e l'ultima fase
    assert [ws.review_of(s, ws.stages()) for s in ws.stages()] == [True, False, True]
    outside = procedure.parse_contract("## Inputs\n- ../../../../etc/passwd\n## Outputs\n- a.md", s2.dir, ws.path)
    assert outside.inputs == []  # niente fuori dalla cartella della procedura
    assert procedure.find("fai il resoconto", tmp_path / "Procedure").name == "Resoconto spese"


def test_run_review_correct_and_continue(tmp_path):
    ws = make(tmp_path)
    calls = []
    model = Model([
        {"content": "", "tool_calls": [{"function": {"name": "calculate", "arguments": json.dumps({"expression": "61.2+38.8"})}}]},
        {"content": "Casa: 100 €\nTotale: 100 €"},
        {"content": "=== resoconto.md ===\nA ottobre abbiamo speso 100 € per la casa.\n=== titolo.txt ===\nOttobre"},
    ])
    notes = []
    r = procedure.Runner(ws, model, tools(calls), wait=lambda: True, notify=lambda t, m: notes.append(m))
    first = r.run()
    # la prima fase sono solo azioni: niente modello, e si ferma per la revisione
    assert first.status == "da guardare" and first.stage == "01_raccolta" and not model.seen
    raccolta = ws.path / "stages/01_raccolta/output/bollette.md"
    assert "61,20" in raccolta.read_text() and calls == [("search_files", "bollette ottobre")] and notes
    assert r.run().status == "da guardare"  # finché l'utente non dice «vai avanti»
    raccolta.write_text(raccolta.read_text() + "Acqua: 0 € (corretto a mano)\n")  # l'utente corregge
    second = r.proceed()
    assert second.status == "da guardare" and second.stage == "03_resoconto"
    (msgs, offered) = model.seen[0]
    assert offered == ["calculate"]  # solo gli strumenti della fase
    user = msgs[1]["content"]
    assert "corretto a mano" in user  # la fase dopo legge quello che ha lasciato l'utente
    assert user.index("Regole da seguire") < user.index("Materiale da elaborare")
    assert "luce, gas" in user and "Semplice e breve" not in user  # solo la sezione chiesta
    assert "Somma le spese per categoria" in msgs[0]["content"] and "Ogni mese il resoconto" in msgs[0]["content"]
    assert ("calculate", "61.2+38.8") in calls
    last_user = model.seen[-1][0][1]["content"]
    assert "Semplice e breve" in last_user and "Totale: 100" in last_user and "corretto a mano" not in last_user
    assert (ws.path / "stages/03_resoconto/output/titolo.txt").read_text() == "Ottobre\n"
    done = r.proceed()
    assert done.status == "finita" and done.done == ["01_raccolta", "02_conti", "03_resoconto"]
    assert [label for _, label in r.status()] == ["fatta"] * 3
    # com'era prima delle correzioni: per capire cosa l'utente corregge sempre
    assert "corretto a mano" not in (ws.state_dir / "original/01_raccolta/bollette.md").read_text()


def test_changed_reference_redoes_only_what_reads_it(tmp_path):
    ws = make(tmp_path)
    for st in ws.stages():  # tutto già fatto
        st.output_dir.mkdir(parents=True, exist_ok=True)
        for f in st.output_files():
            f.write_text("x\n")
    state = {"stages": {st.key: {"status": "fatta", "fingerprint": procedure.fingerprint(st, ws.path)} for st in ws.stages()}}
    ws.save_state(state)
    assert [label for _, label in procedure.Runner(ws, Model([]), wait=lambda: True).status()] == ["fatta"] * 3
    cat = ws.path / "_config/categorie.md"
    cat.write_text(cat.read_text().replace("Semplice e breve.", "Con un po' di umorismo."))  # cambia solo il Tono
    assert [label for _, label in procedure.Runner(ws, Model([]), wait=lambda: True).status()] == ["fatta", "fatta", "da fare"]
    r = procedure.Runner(ws, Model([{"content": "=== resoconto.md ===\nok\n=== titolo.txt ===\nok"}]), wait=lambda: True)
    assert r.run().stage == "03_resoconto"
    r.redo("conti")
    assert [label for _, label in r.status()] == ["fatta", "da fare", "da fare"]


def test_verify_retry_and_stop(tmp_path):
    ws = make(tmp_path)
    (ws.path / "stages/02_conti/CONTEXT.md").write_text("""## Inputs
- ../01_raccolta/output/
## Process
Somma.
## Outputs
- conti.md
## Verify
- contiene: Totale
- le cifre sono in euro
## Review
no
""")
    st1 = ws.stages()[0]
    st1.output_dir.mkdir(parents=True)
    (st1.output_dir / "bollette.md").write_text("luce 61,20\n")
    ws.save_state({"stages": {st1.key: {"status": "approvata", "fingerprint": procedure.fingerprint(st1, ws.path)}}})
    model = Model([{"content": "Casa 61,20"}, {"content": '{"ok": true}'},       # manca «Totale»: riprova
                   {"content": "Casa 61,20 $"}, {"content": '{"ok": false, "motivo": "dollari"}'}])  # ancora no
    report = procedure.Runner(ws, model, wait=lambda: True).run()
    # non passa i controlli neanche riprovando: si ferma «da guardare» (decide l'utente), il risultato resta
    assert report.status == "da guardare" and report.stage == "02_conti" and "contiene: Totale" in report.message
    retry_prompt = model.seen[2][0][1]["content"]
    assert "Da correggere" in retry_prompt and "contiene: Totale" in retry_prompt
    assert (ws.path / "stages/02_conti/output/conti.md").read_text().startswith("Casa 61,20 $")
    later = procedure.Runner(ws, Model([{"content": "=== resoconto.md ===\nok\n=== titolo.txt ===\nok"}]), wait=lambda: True)
    assert later.status()[1][1] == "da guardare"
    assert later.proceed().stage == "03_resoconto"  # «vai avanti»: si continua con quello che c'è


def test_actions_need_confirmation(tmp_path):
    ws = make(tmp_path)
    (ws.path / "stages/01_raccolta/CONTEXT.md").write_text('## Process\naction: send_mail {"to": "marco@example.org"}\n## Outputs\n- inviata.md\n')
    calls = []
    no = procedure.Runner(ws, Model([]), tools(calls), confirm=lambda n, a: False, wait=lambda: True).run()
    assert no.status == "ferma" and "non hai confermato" in no.message and calls == []
    yes = procedure.Runner(ws, Model([]), tools(calls), confirm=lambda n, a: a == {"to": "marco@example.org"}, wait=lambda: True).run()
    assert yes.status == "da guardare" and calls == [("send_mail", "marco@example.org")]
