import json
import time
from datetime import date

import pytest

from aios_copilot import cloud
from aios_copilot.agent import Agent
from aios_copilot.tools.base import Tool, params


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-prova")
    monkeypatch.setattr(cloud, "key", lambda: "sk-or-prova")


def reply(content="", cost=0.002, calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"id": "c1", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for n, a in calls]
    return {"choices": [{"message": msg}], "usage": {"cost": cost}}


def test_cloud_model_speaks_openai_and_counts_the_cost():
    sent = []
    m = cloud.CloudModel("anthropic/claude-x", api_key="k", post=lambda url, payload, key: sent.append(payload) or reply(calls=[("calculate", {"espressione": "1+1"})]))
    out = m.chat([{"role": "user", "content": "ciao"}], [{"type": "function", "function": {"name": "calculate"}}])
    assert out["tool_calls"][0]["function"] == {"name": "calculate", "arguments": {"espressione": "1+1"}}
    assert sent[0]["model"] == "anthropic/claude-x" and sent[0]["usage"] == {"include": True}
    day, month, n = cloud.Usage().spent()
    assert n == 1 and day == pytest.approx(0.002)


def test_limits_stop_the_cloud():
    cloud.save_settings({"attivo": True, "limite_giorno": 0.01})
    u = cloud.Usage()
    assert cloud.budget_left() == ""
    u.add(0.02, "x")
    assert "limite di oggi" in cloud.budget_left()
    e = cloud.Escalation(make=lambda: "cloud")
    assert e.choose("perché il cielo è blu?", "ragionamento") is None and "limite" in e.note


def test_choose_only_hard_or_asked():
    cloud.save_settings({"attivo": True})
    e = cloud.Escalation(make=lambda: "cloud")
    assert e.choose("alza il volume", "azione") is None
    assert e.choose("che ore sono", "risposta") is None
    assert e.choose("confronta queste offerte", "ragionamento") == "cloud"
    assert e.choose("chiedilo al modello grande: ciao", "risposta") == "cloud"
    cloud.save_settings({"attivo": False})
    assert e.choose("confronta queste offerte", "ragionamento") is None


def test_pick_newest_and_free():
    ms = [{"id": "anthropic/claude-old", "nome": "Claude Old", "gratis": False, "creato": 1},
          {"id": "anthropic/claude-new", "nome": "Claude New", "gratis": False, "creato": 9},
          {"id": "deepseek/deepseek-chat:free", "nome": "DeepSeek (free)", "gratis": True, "creato": 5},
          {"id": "deepseek/deepseek-chat", "nome": "DeepSeek", "gratis": False, "creato": 6}]
    assert cloud.pick("claude", ms)["id"] == "anthropic/claude-new"
    assert cloud.pick("deepseek gratis", ms)["id"] == "deepseek/deepseek-chat:free"
    assert cloud.pick("llama", ms) is None


class Local:
    supports_stream = True

    def __init__(self, replies, delay=0.0):
        self.replies, self.delay, self.think = list(replies), delay, False

    def chat(self, messages, tools, on_token=None):
        r = self.replies.pop(0)
        for piece in (r.get("content") or "").split():
            time.sleep(self.delay)
            if on_token:
                on_token(piece + " ")
        return r


class Cloud:
    supports_stream = False
    is_cloud = True
    label = "claude"
    model = "anthropic/claude"

    def __init__(self, replies):
        self.replies = list(replies)
        self.think = False

    def chat(self, messages, tools):
        return self.replies.pop(0)


class Esc:
    def __init__(self, cloud_model, route_cloud=False, wait=5.0, privacy="chiedi"):
        self.cloud, self.route_cloud, self.wait, self.privacy, self.note = cloud_model, route_cloud, wait, privacy, ""

    def choose(self, text, route):
        return self.cloud if self.route_cloud else None

    def local_wait(self):
        return self.wait

    def fallback(self):
        return self.cloud

    def private_ok(self):
        return self.privacy


def test_hard_requests_go_straight_to_the_cloud():
    events = []
    agent = Agent(Local([]), [], confirm=lambda *a, **k: True, escalation=Esc(Cloud([{"content": "Risposta del modello grande."}]), route_cloud=True))
    assert agent.ask("scrivimi un piano", on_event=lambda k, d: events.append(k)) == "Risposta del modello grande."
    assert "cloud" in events


def test_slow_local_model_hands_over_after_the_wait():
    events = []
    local = Local([{"content": "una risposta lentissima " * 20}], delay=0.01)
    agent = Agent(local, [], confirm=lambda *a, **k: True, escalation=Esc(Cloud([{"content": "Fatto dal cloud."}]), wait=0.05))
    assert agent.ask("dimmi qualcosa", on_event=lambda k, d: events.append(k)) == "Fatto dal cloud."
    assert events.index("retry") < events.index("cloud")
    assert agent.model is local  # finita la richiesta si torna al modello del PC


def test_fast_local_model_stays_local():
    agent = Agent(Local([{"content": "ciao"}]), [], confirm=lambda *a, **k: True, escalation=Esc(Cloud([]), wait=5))
    assert agent.ask("ciao") == "ciao"


def test_private_data_never_leaves_if_the_user_said_so():
    from aios_copilot.pianifica import Planner

    tools = [Tool("search_mail", "", params(query="q"), lambda query="": "[1] da Sara: bolletta", reads_private=True)]
    local = Local([{"content": "1. cerco\n2. leggo"}, {"content": "Ecco la mail di Sara."}])
    agent = Agent(local, tools, confirm=lambda *a, **k: True, pianificatore=Planner(local, today=date.today),
                  escalation=Esc(Cloud([{"content": "NO"}]), route_cloud=True, privacy="mai"))
    assert agent.ask("leggi la mail di Sara sulla bolletta") == "Ecco la mail di Sara."
