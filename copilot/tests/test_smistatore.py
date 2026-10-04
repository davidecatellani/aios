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


def test_streaming_answer_arrives_piece_by_piece():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from aios_copilot import llm

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["stream"] is True and body["think"] is False
            self.send_response(200)
            self.end_headers()
            for piece in ("Ciao", ", sono", " Nova."):
                self.wfile.write(json.dumps({"message": {"content": piece}, "done": False}).encode() + b"\n")
            self.wfile.write(json.dumps({"message": {"content": ""}, "done": True}).encode() + b"\n")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    client = llm.OllamaClient(url=f"http://127.0.0.1:{srv.server_address[1]}", model="qwen3.5:2b")
    pieces = []
    reply = client.chat([{"role": "user", "content": "ciao"}], [], on_token=pieces.append)
    assert pieces == ["Ciao", ", sono", " Nova."] and reply["content"] == "Ciao, sono Nova."
