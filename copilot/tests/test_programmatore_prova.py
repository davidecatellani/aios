import shutil
import sys
from pathlib import Path

import pytest

from aios_copilot import codice, programmatore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addestramento"))
import programmatore_prova as pp  # noqa: E402

if shutil.which("git") is None:
    pytest.skip("git non c'è", allow_module_level=True)


class Fake:
    """Un modello finto con le risposte già scritte, che conta le richieste come Measured."""

    def __init__(self, steps):
        self.steps, self.calls = list(steps), []

    def chat(self, messages, tools):
        self.calls.append({"prompt": sum(len(str(m.get("content", ""))) for m in messages) // 4, "risposta": 10})
        return self.steps.pop(0)


def call(name, **args):
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_measure_both_modes(tmp_path, monkeypatch):
    for k in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(k, str(tmp_path / k))
    base = tmp_path / "base" / "aios_copilot"
    shutil.copytree(Path(programmatore.__file__).parent, base, ignore=shutil.ignore_patterns("__pycache__"))
    codice.ensure(base)
    monkeypatch.setattr(codice, "check", lambda: (True, "ok"))  # i moduli veri qui non si caricano tutti
    task = pp.TASKS["meteo"]
    edit = call("modifica_file", percorso=pp.WIDGET, vecchio="METEO_EVERY = 900", nuovo="METEO_EVERY = 600")
    free = pp.run_one(task, "libero", Fake([edit, call("controlla"), call("fatto", riassunto="Ogni 10 minuti.")]))
    staged = pp.run_one(task, "fasi", Fake([{"content": '{"file": ["aios_copilot/widget.py"], "cerca": ["METEO_EVERY"], '
                                                        '"cosa": "meteo ogni 10 minuti"}'},
                                            edit, {"content": "fatto"}, {"content": "Il meteo si aggiorna ogni 10 minuti."}]))
    for r in (free, staged):
        assert r["tutto_giusto"] and r["finito"] and r["file"] == [pp.WIDGET] and "+METEO_EVERY = 600" in r["diff"]
    assert free["richieste"] == 3 and staged["richieste"] == 4
    assert not codice.changed_files()  # dopo ogni giro la copia torna com'era
    wrong = pp.run_one(task, "libero", Fake([call("modifica_file", percorso=pp.HOME, vecchio="--turchese: #2EC4B6",
                                                  nuovo="--turchese: #000000"), call("controlla"), call("fatto", riassunto="x")]))
    assert not wrong["tutto_giusto"] and dict(wrong["controlli"])["solo i file giusti"] is False
    table = pp.table([{**r, "compito": "meteo"} for r in (free, staged, wrong)])
    assert "| meteo | libero | 1/2 |" in table and "| **totale** | fasi | 1/1 |" in table
