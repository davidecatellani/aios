import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addestramento"))
import livelli_prova as lp  # noqa: E402


class Perfect:
    """Un modello finto che sa le risposte: dal bronze ripete quella giusta, dal silver legge la mail."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def chat(self, messages, tools):
        self.calls.append({"prompt": len(messages[-1]["content"]) // 4, "risposta": 10})
        text = messages[-1]["content"]
        if "Quali sono le cose da fare?" in text:
            return {"content": self.answer}
        m = lp.re.search(r"di (.+?) entro il (\d+ \w+)", text)
        if m:
            return {"content": json.dumps({"tipo": "da fare", "cosa": m.group(1), "entro": m.group(2)})}
        return {"content": '{"tipo": "informazione", "cosa": "", "entro": ""}'}


def test_box_bronze_and_gold(tmp_path, monkeypatch):
    task = lp.task_posta(lp.random.Random("livelli-0"))
    mails = lp.mails_of(task.files["input/posta.md"])
    assert len(mails) == 20 and all(a and "@" in b for a, b, _, _ in mails)
    model = Perfect(task.answer)
    r = lp.run_box(0, model, tmp_path)
    assert all(ok for _, ok in r["bronze"]["controlli"])
    assert all(ok for _, ok in r["gold"]["controlli"]), r["gold"]["risposta"]
    assert r["gold"]["mail_lette"] == 20 and r["gold"]["richieste_al_modello"] < 20  # la pubblicità senza modello
    assert "| gold (letto prima, risponde il codice) | 1/1 |" in lp.table([r])
