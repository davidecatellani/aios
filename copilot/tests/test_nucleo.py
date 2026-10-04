import json
from datetime import datetime

from aios_copilot import nucleo, smistatore as sm
from aios_copilot.tools.base import Tool, params


def tool(name, **props):
    return Tool(name, f"Fa {name}", params(**props), lambda **k: "ok")


class FakeServer:
    """llama-server finto: risponde con la prima opzione della grammatica o con un JSON fisso."""

    def __init__(self, choice=None, prob=0.9, fields=None):
        self.choice, self.prob, self.fields, self.calls = choice, prob, fields or {}, []

    def get(self, url, timeout):
        assert url.endswith("/lora-adapters")
        return [{"id": 0, "path": "/usr/share/aios/nucleo/smistamento.gguf", "scale": 0.0},
                {"id": 1, "path": "/usr/share/aios/nucleo/campi.gguf", "scale": 0.0}]

    def post(self, url, payload, timeout):
        self.calls.append(payload)
        if "json_schema" in payload:
            return {"content": json.dumps(self.fields), "completion_probabilities": []}
        options = json.loads("[" + payload["grammar"].split("::=", 1)[1].replace(" | ", ",") + "]")
        choice = self.choice if self.choice in options else options[0]
        return {"content": choice, "completion_probabilities": [{"token": choice, "logprob": -0.01},
                                                                 {"token": "x", "prob": self.prob}]}


def test_prompts_are_stable():
    p = nucleo.prompt_campi("ricordami il latte domani", "add_reminder", "Crea un promemoria",
                            {"what": {"description": "Cosa"}, "repeat": {"description": "Ripeti", "enum": ["", "daily"]}},
                            datetime(2026, 10, 5, 9, 30))
    assert "Campi: what (Cosa); repeat (Ripeti: |daily)" in p and "Adesso: lunedì 2026-10-05 09:30" in p
    assert p.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")
    assert nucleo.grammar_choice(["agenda", "posta"]) == 'root ::= "agenda" | "posta"'


def test_choose_selects_only_its_adapter_and_reports_confidence():
    srv = FakeServer(choice="posta", prob=0.7)
    n = nucleo.Nucleo("http://x", post=srv.post, get=srv.get)
    assert n.available()
    assert n.choose(nucleo.prompt_ambito("leggi la posta"), ["agenda", "posta"]) == ("posta", 0.7)
    assert srv.calls[-1]["lora"] == [{"id": 0, "scale": 1.0}, {"id": 1, "scale": 0.0}]


def test_missing_server_is_not_available():
    def down(url, timeout):
        raise OSError("connection refused")
    assert not nucleo.Nucleo("http://x", get=down).available()


def test_smistatore_uses_nucleo_instead_of_tev1():
    srv = FakeServer(choice="agenda", fields={"what": "il latte", "when": "2026-10-06T09:00"})
    groups = {"agenda": [tool("add_reminder", what="Cosa", when="Quando")], "posta": [tool("read_mail")]}
    tev1 = []
    s = sm.build(groups, post=lambda u, p, t: tev1.append(u) or {}, nucleo=nucleo.Nucleo("http://x", post=srv.post, get=srv.get))
    assert s.narrow("ricordami il latte domani") == {"add_reminder"}
    plan = s.plan("ricordami il latte domani", {t.name: t for g in groups.values() for t in g})
    assert plan == ("add_reminder", {"what": "il latte", "when": "2026-10-06T09:00"})
    assert not tev1 and s.last["da"] == "nucleo"
    assert srv.calls[-1]["lora"] == [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 1.0}]  # campi: l'altro adattatore


def test_dates_come_from_rules_not_from_the_model():
    props = {"what": {"description": "Cosa ricordare"}, "when": {"description": "Quando (ISO)"}}
    now = datetime(2026, 10, 4, 21, 30)
    fixed = sm.fix_dates({"what": "chiamare Luca", "when": "2026-10-04T18:00"}, props,
                         "ricordami di chiamare Luca domani alle 18", now)
    assert fixed == {"what": "chiamare Luca", "when": "2026-10-05T18:00"}
    assert sm.fix_dates({"what": "latte"}, props, "ricordami il latte", now) == {"what": "latte"}
    assert sm.fix_dates({"name": "Spotify"}, {"name": {"description": "Nome"}}, "chiudi spotify domani", now) == {"name": "Spotify"}
