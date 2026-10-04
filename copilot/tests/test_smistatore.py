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


def test_tasks_are_split_between_small_models():
    """Tev1 sceglie ambito e azione, il modello piccolo compila i campi: il modello grande non lavora."""
    import json as _json

    calls = []
    reminder = Tool("add_reminder", "Crea un promemoria.", params(["what"], what="Cosa ricordare", when="Quando"),
                    lambda what, when="": f"Ok: {what} {when}")
    status = tool("energy_status")

    def post(url, payload, timeout):
        calls.append(url.rsplit("/", 1)[-1] if "systemone" not in url else list(payload["questions"])[0])
        if url.endswith("/v1/systemone"):
            q = list(payload["questions"])[0]
            choice = {"ambito": "agenda", "azione": "add_reminder"}[q]
            return {"answers": {q: {"choice": choice, "confidence": 0.9}}}
        assert payload["model"] == "qwen3.5:0.8b" and payload["format"]["required"] == ["what"]
        return {"message": {"content": _json.dumps({"what": "pagare la bolletta", "when": "2026-10-05T12:00"})}}

    s = sm.build({"agenda": [reminder], "computer": [status]}, post=post)
    a = Agent(Model(), [reminder, status], confirm=lambda *x, **k: True, narrow=s.narrow, planner=s.plan)
    assert a.ask("segnati che domani a mezzogiorno devo pagare la bolletta") == "Ok: pagare la bolletta 2026-10-05T12:00"
    assert calls == ["ambito", "azione", "chat"]  # una sola scelta d'ambito (in cache), niente modello grande


def test_unsure_action_goes_to_the_big_model():
    reminder = Tool("add_reminder", "Crea un promemoria.", params(["what"], what="Cosa ricordare"), lambda what: what)

    def post(url, payload, timeout):
        q = list(payload["questions"])[0]
        return {"answers": {q: {"choice": "agenda" if q == "ambito" else "conversazione", "confidence": 0.9}}}

    s = sm.build({"agenda": [reminder]}, post=post)
    model = Model()
    Agent(model, [reminder], confirm=lambda *x, **k: True, narrow=s.narrow, planner=s.plan).ask("com'è organizzata l'agenda?")
    assert model.seen[-1] == ["add_reminder"]  # il modello grande decide, con i soli strumenti dell'agenda
