import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addestramento"))

import procedure_prova as pp  # noqa: E402


def test_numbers_in_italian_and_english():
    assert pp.numbers("Totale 1.234,50 € e 12,5 poi 7 e 3.20") == [1234.5, 12.5, 7.0, 3.2]
    assert pp.has_number(1234.5)("totale: 1234,50") and not pp.has_number(1234.5)("1234,60")


def test_checks_accept_a_right_answer_and_reject_an_empty_one():
    for name, make in pp.TASKS.items():
        for i in range(5):
            task = make(random.Random(f"x-{name}-{i}"))
            assert all(ok for _, ok in pp.score(task, task.answer)), (name, i, [c for c, ok in pp.score(task, task.answer) if not ok])
            assert not any(ok for c, ok in pp.score(task, "") if not c.startswith("senza"))


class Fake:
    """Un modello finto: risponde sempre con la risposta giusta (il lavoro deve arrivare in fondo)."""

    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def chat(self, messages, tools):
        self.prompts.append(sum(len(m["content"]) for m in messages))
        self.calls.append({"prompt": self.prompts[-1] // 4, "risposta": 10, "secondi": 0})
        return {"content": self.answer}


def test_one_prompt_reads_everything_and_stages_read_less(tmp_path):
    task = pp.task_preventivo(random.Random(3))
    model = Fake(task.answer)
    model.calls = []
    single = pp.run_one(task, "unico", tmp_path, model)
    staged = pp.run_one(task, "fasi", tmp_path, model)
    assert single["tutto_giusto"] and staged["tutto_giusto"] and single["richieste"] == 1 and staged["richieste"] == 3
    unico = pp.write_workspace(task, tmp_path / "w", "unico").stages()
    assert len(unico) == 1 and unico[0].contract.tools == ["calculate"]
    assert {i.path.name for i in unico[0].contract.inputs} == {"richiesta.md", "listino.md", "regole.md"}
    assert "Passo 3" in unico[0].contract.process
    assert "| preventivo | fasi |" in pp.table([dict(staged, compito="preventivo"), dict(single, compito="preventivo")])
