import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addestramento"))
import velocita_prova as v  # noqa: E402


def test_scenario_and_prompts():
    order = v.scenario()
    assert len(order) == 12 and [k for k, _ in order[:4]] == ["nova", "codice", "nova", "posta"]
    messages, tools = v.nova(0)
    assert "Nova" in messages[0]["content"] and len(tools) > 10
    messages, tools = v.codice(0)
    assert "--turchese" in messages[1]["content"] and {t["function"]["name"] for t in tools} >= {"modifica_file"}
    messages, tools = v.posta(0)
    assert tools == [] and len(messages[1]["content"]) > 6000


def test_llama_reply_and_table(monkeypatch):
    sent = []

    def post(url, payload, timeout=0):
        sent.append(payload)
        return {"choices": [{"message": {"content": "ok"}}],
                "timings": {"prompt_n": 30, "prompt_ms": 1500, "predicted_n": 20, "predicted_ms": 2000, "draft_n": 10, "draft_n_accepted": 7}}

    monkeypatch.setattr(v, "_post", post)
    engine = object.__new__(v.Llama)
    engine.url, engine.slots = "http://x", 3
    r = engine.ask("posta", [{"role": "user", "content": "ciao"}], [])
    assert sent[0]["id_slot"] == 2 and sent[0]["chat_template_kwargs"] == {"enable_thinking": False} and "tools" not in sent[0]
    assert r["attesa"] == 1.5 and r["scritti"] == 20 and r["bozza_presa"] == 7
    rows = [{**r, "tipo": "posta", "n": 0}]
    results = [{"modo": "llama-1", "errore": "", "ram_gb": 3.1, "richieste": rows},
               {"modo": "llama-3-bozza", "errore": "", "ram_gb": 4.0, "richieste": [{**rows[0], "totale": r["totale"] / 2 or 1}]},
               {"modo": "ollama-fa-q8", "errore": "RuntimeError: no", "richieste": []}]
    text = v.table(results)
    assert "| llama-1 | 1.5 s | 10.0 |" in text and "| 70% | 1/1 |" in text and "non parte: RuntimeError: no" in text
