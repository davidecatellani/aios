"""Lo smistatore: un modello decisionale sceglie l'ambito, il modello di conversazione vede solo quegli strumenti."""

from aios_copilot import smistatore as sm
from aios_copilot.agent import Agent
from aios_copilot.tools.base import Tool, params


def tool(name):
    return Tool(name, f"strumento {name}", params(), lambda: f"fatto {name}")


def fake_post(choice, confidence, calls):
    def post(url, payload, timeout):
        calls.append((url, payload))
        q = payload["questions"]["ambito"]
        assert q["type"] == "choice" and 2 <= len(q["criteria"]) <= 26  # limiti di System One
        return {"model": payload["model"], "answers": {"ambito": {"type": "choice", "choice": choice,
                "probabilities": {choice: confidence}, "confidence": confidence}}}
    return post


class Model:
    def __init__(self):
        self.seen = []

    def chat(self, messages, tools):
        self.seen.append([t["function"]["name"] for t in tools])
        return {"content": "ok"}


def test_only_the_domain_tools_reach_the_model():
    calls = []
    groups = {"agenda": [tool("add_reminder"), tool("list_events")], "posta": [tool("read_mail")]}
    s = sm.build(groups, post=fake_post("agenda", 0.9, calls))
    model = Model()
    a = Agent(model, [t for g in groups.values() for t in g], confirm=lambda *x, **k: True, narrow=s.narrow)
    assert a.ask("ricordami di chiamare la mamma") == "ok"
    assert model.seen[-1] == ["add_reminder", "list_events"]
    assert calls[0][0].endswith("/v1/systemone") and calls[0][1]["model"] == "tev1:0.8b"


def test_unsure_or_missing_decision_model_gives_all_tools():
    groups = {"agenda": [tool("add_reminder")], "posta": [tool("read_mail")]}
    unsure = sm.build(groups, post=fake_post("agenda", 0.3, []))
    assert unsure.narrow("boh") is None

    def broken(url, payload, timeout):
        raise OSError("model 'tev1:0.8b' not found")
    missing = sm.build(groups, post=broken)
    assert missing.narrow("leggi la posta") is None
    calls = []
    missing.post = fake_post("posta", 0.99, calls)
    assert missing.narrow("leggi la posta") is None and not calls  # dopo un errore si riprova più tardi, non a ogni frase
    model = Model()
    Agent(model, [t for g in groups.values() for t in g], confirm=lambda *x, **k: True, narrow=unsure.narrow).ask("boh")
    assert model.seen[-1] == ["add_reminder", "read_mail"]


def test_chat_domain_has_no_tools():
    s = sm.build({"agenda": [tool("add_reminder")]}, post=fake_post("chiacchiera", 0.95, []))
    assert s.narrow("chi ha scritto i Promessi sposi?") == set()
