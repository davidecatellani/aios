import json
import subprocess
from pathlib import Path

import pytest

from aios_copilot import anteprima, codice, programmatore

if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
    pytest.skip("git non c'è", allow_module_level=True)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(anteprima, "can_render", lambda: False)  # niente foto vere delle pagine nelle prove
    base = tmp_path / "base" / "aios_copilot"
    (base / "shell").mkdir(parents=True)
    (base / "__init__.py").write_text('"""base"""\n')
    (base / "shell" / "__init__.py").write_text("")
    (base / "shell" / "home.html").write_text("<html><style>.orologio { border-radius: 0; }</style><script>let a = [1];</script></html>\n")
    (base / "widget.py").write_text("RAGGIO = 0\n")
    return base


def fake_check():
    return True, "ok"


class Script:
    """Un modello finto che fa il programmatore: cerca, legge, modifica, controlla, finisce."""

    def __init__(self, steps):
        self.steps = list(steps)

    def chat(self, messages, tools):
        return self.steps.pop(0)


def call(name, **args):
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_copy_customize_undo_share_import(home, tmp_path, monkeypatch):
    monkeypatch.setattr(codice, "check", fake_check)
    codice.ensure(home)
    assert codice.exists() and not codice.history() and not codice.state()["attivo"]
    model = Script([
        call("cerca", testo="border-radius", cartella="aios_copilot"),
        call("leggi", percorso="aios_copilot/shell/home.html"),
        call("modifica_file", percorso="aios_copilot/codice.py", vecchio="x", nuovo="y"),  # protetto
        call("modifica_file", percorso="../fuori.txt", vecchio="x", nuovo="y"),  # fuori dalla copia
        call("fatto", riassunto="troppo presto"),  # prima va controllato
        call("modifica_file", percorso="aios_copilot/shell/home.html", vecchio="border-radius: 0", nuovo="border-radius: 50%"),
        call("controlla"),
        call("fatto", riassunto="L'orologio adesso è rotondo (home.html)."),
    ])
    seen = []
    r = programmatore.customize("voglio l'orologio rotondo", model=model, on_step=seen.append)
    assert r["ok"] and r["file"] == ["aios_copilot/shell/home.html"], r
    assert "border-radius: 50%" in (codice.root() / "aios_copilot/shell/home.html").read_text()
    assert "border-radius: 0" in (home / "shell/home.html").read_text()  # la base non si tocca
    h = codice.history()
    assert [x["richiesta"] for x in h] == ["voglio l'orologio rotondo"] and codice.state()["attivo"]

    # condividere: file .aios → un altro utente lo vede e lo importa
    shared = codice.export(h[0]["id"], tmp_path / "condivisi")
    info = codice.read_shared(shared)
    assert info["file"] == ["aios_copilot/shell/home.html"] and info["rischi"] == []

    # annullare
    ok, what = codice.undo("orologio")
    assert ok and what == "voglio l'orologio rotondo" and codice.history() == [] and not codice.state()["attivo"]
    assert "border-radius: 0" in (codice.root() / "aios_copilot/shell/home.html").read_text()

    ok, what = codice.import_shared(shared)
    assert ok and "border-radius: 50%" in (codice.root() / "aios_copilot/shell/home.html").read_text()
    assert len(codice.history()) == 1

    # aggiornamento della base: le modifiche ci vanno sopra
    (home / "widget.py").write_text("RAGGIO = 2  # nuova versione\n")
    monkeypatch.setattr(codice, "base_version", lambda: "2026.11")
    assert codice.sync_base(home) == "aggiornata"
    assert "nuova versione" in (codice.root() / "aios_copilot/widget.py").read_text()
    assert "border-radius: 50%" in (codice.root() / "aios_copilot/shell/home.html").read_text()

    assert codice.reset_all() == 1 and codice.history() == [] and not codice.state()["attivo"]


def test_risky_changes_wait_for_yes(home, monkeypatch):
    monkeypatch.setattr(codice, "check", fake_check)
    codice.ensure(home)
    model = Script([
        call("modifica_file", percorso="aios_copilot/widget.py", vecchio="RAGGIO = 0",
             nuovo="import subprocess\nsubprocess.run(['curl', 'x'])\nRAGGIO = 0"),
        call("controlla"), call("fatto", riassunto="fatto"),
    ])
    r = programmatore.customize("manda i dati", model=model)
    assert not r["ok"] and r["in_attesa"] and any("subprocess" in x for x in r["rischi"])
    assert codice.history() == []
    assert programmatore.apply_pending()["ok"] and len(codice.history()) == 1


def test_failed_attempt_is_discarded(home, monkeypatch):
    monkeypatch.setattr(codice, "check", fake_check)
    codice.ensure(home)
    model = Script([call("modifica_file", percorso="aios_copilot/widget.py", vecchio="RAGGIO = 0", nuovo="RAGGIO = 1")] +
                   [{"content": "non so", "tool_calls": []}] * programmatore.MAX_STEPS)
    r = programmatore.customize("boh", model=model)
    assert not r["ok"] and codice.changed_files() == []


def test_check_finds_real_errors(home):
    codice.ensure(home)
    (codice.root() / "aios_copilot/widget.py").write_text("def x(:\n")
    ok, msg = codice.check()
    assert not ok and "sintassi" in msg
    (codice.root() / "aios_copilot/widget.py").write_text("RAGGIO = 0\n")
    (codice.root() / "aios_copilot/shell/home.html").write_text("<html><script>if (a) { b(</script></html>")
    ok, msg = codice.check()
    assert not ok and "parentesi" in msg


def test_package_runs_from_personal_copy(home, tmp_path):
    codice.ensure(home)
    codice.save_state(attivo=True)
    root = Path(__file__).resolve().parents[1]
    env = {"HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "data"), "XDG_STATE_HOME": str(tmp_path / "state"), "PATH": "/usr/bin:/bin"}
    code = "import aios_copilot; print(aios_copilot.__path__[0])"
    out = subprocess.run(["python3", "-c", code], cwd=root, env=env, capture_output=True, text=True).stdout.strip()
    assert out == str(codice.root() / "aios_copilot")
    out = subprocess.run(["python3", "-c", code], cwd=root, env={**env, "AIOS_CODICE": "base"}, capture_output=True, text=True).stdout.strip()
    assert out == str(root / "aios_copilot")
    # modalità sicura: tre avvii della shell senza «sto bene» → si torna all'originale
    shell = "import sys; sys.argv = ['aios-shell']; import aios_copilot; print(aios_copilot.PERSONAL_DIR is not None)"
    runs = [subprocess.run(["python3", "-c", shell], cwd=root, env=env, capture_output=True, text=True).stdout.strip() for _ in range(3)]
    assert runs == ["True", "True", "False"] and codice.state()["guasto"]


def test_nova_tools_and_router(home, monkeypatch):
    from aios_copilot.tools.personalizza import CustomizeRouter, make_tools

    monkeypatch.setattr(codice, "check", fake_check)
    restarts = []
    tools = {t.name: t for t in make_tools(restart=lambda: restarts.append(1))}
    monkeypatch.setattr(programmatore, "pick_model", lambda: (Script([
        call("modifica_file", percorso="aios_copilot/widget.py", vecchio="RAGGIO = 0", nuovo="RAGGIO = 12"),
        call("controlla"), call("fatto", riassunto="Bordi più tondi (widget.py)."),
    ]), "finto"))
    codice.ensure(home)
    out = tools["customize_system"].func("bordi più tondi")
    assert out.startswith("Bordi più tondi") and restarts == [1]
    assert "bordi più tondi" in tools["list_customizations"].func()
    assert tools["share_customization"].func("bordi").endswith("decide se applicarlo.")
    assert tools["undo_customization"].func("ultima").startswith("Ho tolto «bordi più tondi»") and len(restarts) == 2
    assert tools["reset_customizations"].requires_confirmation
    r = CustomizeRouter()
    assert r.match("togli l'ultima personalizzazione").args == {"quale": "ultima"}
    assert r.match("annulla la personalizzazione dell'orologio").args == {"quale": "orologio"}
    assert r.match("rimetti aios originale").tool == "reset_customizations"
    assert r.match("quali personalizzazioni ho?").tool == "list_customizations"
    assert r.match("modifica SoIA: voglio l'orologio rotondo").args == {"richiesta": "voglio l'orologio rotondo"}


def test_retouch_with_the_pencil(home, monkeypatch):
    monkeypatch.setattr(codice, "check", fake_check)
    codice.ensure(home)

    def edit(old, new, summary):
        return Script([call("modifica_file", percorso="aios_copilot/widget.py", vecchio=old, nuovo=new),
                       call("controlla"), call("fatto", riassunto=summary)])

    assert programmatore.customize("orologio rotondo", model=edit("RAGGIO = 0", "RAGGIO = 50", "Tondo."))["ok"]
    seen = []

    class Spy(Script):
        def chat(self, messages, tools):
            seen.append(messages[1]["content"])
            return super().chat(messages, tools)

    spy = Spy(edit("RAGGIO = 50", "RAGGIO = 50\nBORDO = 'turchese'", "Bordo turchese.").steps)
    assert programmatore.customize("col bordo turchese", model=spy, retouch="orologio")["ok"]
    assert "orologio rotondo" in seen[0] and "RAGGIO = 50" in seen[0]  # l'agente vede cosa ritocca
    h = codice.history()
    assert len(h) == 1 and h[0]["richiesta"] == "orologio rotondo" and "Ritocco: col bordo turchese" in h[0]["dettagli"]
    # ritocco di una personalizzazione più vecchia: una modifica a parte, col suo nome
    (home.parent / "x").mkdir(exist_ok=True)
    assert programmatore.customize("altro", model=Script([call("scrivi_file", percorso="aios_copilot/nuovo.py", contenuto="X = 1\n"),
                                                          call("controlla"), call("fatto", riassunto="Nuovo.")]))["ok"]
    assert programmatore.customize("più grande", model=edit("RAGGIO = 50", "RAGGIO = 80", "Più grande."), retouch="orologio")["ok"]
    assert codice.history()[0]["richiesta"] == "Ritocco a «orologio rotondo»: più grande"


def test_visual_check_before_finishing(home, monkeypatch, tmp_path):
    """Una pagina cambiata va guardata: gli errori nuovi si correggono, quelli che c'erano già non contano."""
    monkeypatch.setattr(codice, "check", fake_check)
    codice.ensure(home)
    shots = iter([{"errori": ["ReferenceError: lancette (riga 9 di home.html)"], "problemi": []},
                  {"errori": [], "problemi": ["testo tagliato: «vecchio» .barra"]}])

    def shoot(page):
        img = tmp_path / f"{page.replace('/', '-')}-{len(list(tmp_path.glob('*.png')))}.png"
        img.write_bytes(b"png")
        img.with_suffix(".json").write_text(json.dumps(next(shots)))
        return img, ""

    def shoot_base(page):
        img = tmp_path / "base.png"
        img.write_bytes(b"png")
        img.with_suffix(".json").write_text(json.dumps({"errori": [], "problemi": ["testo tagliato: «vecchio» .barra"]}))
        return img, ""

    prompts = []
    eyes = anteprima.Eyes(codice.root(), "orologio rotondo", shoot=shoot, shoot_base=shoot_base,
                          see=lambda img, prompt: prompts.append(prompt) or "Le lancette segnano le 10:10.")
    model = Script([
        call("modifica_file", percorso="aios_copilot/shell/home.html", vecchio="border-radius: 0", nuovo="border-radius: 50%"),
        call("controlla"),
        call("fatto", riassunto="presto"),  # la pagina non è stata guardata
        call("guarda", pagina="casa", domanda="le lancette sono giuste?"),
        call("fatto", riassunto="presto"),  # c'è un errore di JavaScript
        call("modifica_file", percorso="aios_copilot/shell/home.html", vecchio="let a = [1];", nuovo="let a = [2];"),
        call("controlla"),
        call("guarda", pagina="casa"),
        call("fatto", riassunto="Orologio rotondo."),
    ])
    p = programmatore.Programmer(model, eyes=eyes)
    ok, summary = p.run("orologio rotondo")
    assert ok and summary == "Orologio rotondo." and p.looks == 2 and not model.steps
    assert "le lancette sono giuste?" in prompts[0] and "orologio rotondo" in prompts[0]


def test_page_address_and_report(tmp_path):
    base = "http://127.0.0.1:5/#t=abc"
    assert anteprima.page_url(base, "casa") == (base, "")
    url, js = anteprima.page_url(base, "impostazioni/aspetto")
    assert url == base and "apriVista('impostazioni', 'aspetto')" in js
    assert anteprima.page_url(base, "pannello/emoji")[0] == "http://127.0.0.1:5/static/pannello.html#t=abc&apri=emoji"
    assert anteprima.page_report(tmp_path / "manca.png") == {"errori": [], "problemi": []}
    assert codice.js_balance(anteprima.CHECK_JS) == "" and codice.js_balance(anteprima.CATCH_ERRORS_JS) == ""
