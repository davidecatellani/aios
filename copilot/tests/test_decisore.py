from aios_copilot import giochi
from aios_copilot.agent import Agent
from aios_copilot.decisore import Decisore
from aios_copilot.mail.classify import classify, mail_questions
from aios_copilot.smistatore import Domain, Smistatore


def _answers(**kw):
    return {"answers": kw}


def test_laya_first_then_tev1(monkeypatch):
    monkeypatch.delenv("AIOS_DECISORE", raising=False)
    calls = []

    def post(url, payload, timeout):
        calls.append((url, payload["model"]))
        if "11437" in url:
            raise OSError("Laya spento")
        return _answers(x={"choice": "si", "confidence": 0.9})

    d = Decisore(post=post)
    assert d.choose("frase", "x", {"type": "choice", "criteria": {"si": "", "no": ""}}) == ("si", 0.9)
    assert [m for _, m in calls] == ["nova", "tev1:0.8b"] and d.last_from == "tev1"
    d.choose("frase", "x", {"type": "choice", "criteria": {"si": "", "no": ""}})
    assert [m for _, m in calls][-1] == "tev1:0.8b" and len(calls) == 3  # Laya si riprova più tardi


def test_laya_decides_area_and_route_in_one_call():
    seen = []

    def post(url, payload, timeout):
        seen.append(sorted(payload["questions"]))
        return _answers(ambito={"choice": "chiacchiera", "confidence": 0.1, "answer_confidence": 0.9},
                        percorso={"choice": "ragionamento", "confidence": 0.8})

    judge = Smistatore([Domain("chiacchiera", "talk"), Domain("agenda", "calendar")], nucleo=None,
                       decisore=Decisore(post=post, laya_url="http://127.0.0.1:11437", tev1="spento"))
    assert judge.decide("quanto fa 17 per 23?") == ("chiacchiera", 0.9)
    assert judge.percorso("quanto fa 17 per 23?") == ("ragionamento", 0.8)
    assert seen == [["ambito", "percorso"]]  # una sola chiamata per le due domande


class _Model:
    supports_stream = False

    def __init__(self):
        self.think = False
        self.seen = []

    def chat(self, messages, tools):
        self.seen.append(self.think)
        return {"content": "391"}


def test_reasoning_route_makes_the_model_think():
    model = _Model()
    agent = Agent(model, [], confirm=lambda *a, **k: True, percorso=lambda t: ("ragionamento", 0.9))
    events = []
    assert agent.ask("quanto fa 17 per 23?", on_event=lambda k, d: events.append(k)) == "391"
    assert model.seen == [True] and model.think is False and "thinking" in events
    quick = Agent(model, [], confirm=lambda *a, **k: True, percorso=lambda t: ("risposta", 0.9))
    quick.ask("ciao")
    assert model.seen[-1] is False


def test_mail_category_and_importance_from_laya():
    def decide(state, questions):
        assert "Oggetto: la nonna" in state and set(questions) == set(mail_questions())
        return {"categoria": {"choice": "personali", "confidence": 0.9}, "importanza": {"score": 3.8}}

    v = classify("Sara <sara@gmail.com>", "la nonna", "è in ospedale, chiamami", {}, decide=decide)
    assert v.category == "importanti" and v.importance == 95
    # senza Laya (o se non risponde) restano le regole; la correzione dell'utente vince sempre
    assert classify("Sara <sara@gmail.com>", "ciao", "come stai", {}, decide=lambda s, q: None).category == "personali"
    assert classify("x@shop.it", "ciao", "", {}, override="lavoro", decide=decide).category == "lavoro"


def test_low_memory_unloads_the_chat_model(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    now, free, calls = [0.0], [3.0], []
    mode = giochi.GameMode(unload=lambda: calls.append("scarica") or [], service=lambda a: True,
                           clock=lambda: now[0], free=lambda: free[0])
    mode.tick()
    assert calls == []
    free[0] = 0.5
    mode.tick()
    now[0] = 10
    mode.tick()  # non a ogni giro: al massimo una volta al minuto
    assert calls == ["scarica"]
