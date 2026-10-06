import http.client
import json
import time

import pytest

from aios_copilot import welcome
from aios_copilot.agent import Agent
from aios_copilot.fastpath import Intent
from aios_copilot.tools import Tool
from aios_copilot.tools.base import params


class OneRouter:
    """Riconosce due comandi fissi, come farebbero i livelli 0 e 1."""

    def match(self, text):
        return {"alza il volume": Intent("set_volume", {"action": "up"}),
                "spegni": Intent("power", {"action": "poweroff"})}.get(text)


class NoModel:
    def chat(self, messages, tools):
        from aios_copilot.llm import LLMError

        raise LLMError("modello spento")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    powered = []

    def make_agent(confirm):
        tools = [
            Tool("set_volume", "", params(action="a"), lambda action: "Volume alzato."),
            Tool("power", "", params(action="a"), lambda action: powered.append(action) or "Spegnimento…",
                 requires_confirmation=True),
        ]
        return Agent(NoModel(), tools, confirm, routers=[OneRouter()])

    app = welcome.WelcomeApp(make_agent)
    srv, url = welcome.serve(app)
    port = srv.server_address[1]

    def call(method, path, body=None, token=app.token, host=f"127.0.0.1:{port}"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Host": host, "Content-Type": "application/json"}
        if token is not None:
            headers["X-AIOS-Token"] = token
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, (json.loads(data) if resp.getheader("Content-Type", "").startswith("application/json") else data)

    yield call, url, app, powered
    srv.shutdown()


def wait_job(call, job):
    for _ in range(100):
        status, state = call("GET", f"/api/job/{job}")
        if state["done"] or state["pending"]:
            return state
        time.sleep(0.02)
    raise AssertionError("richiesta non terminata")


def test_page_served_and_token_only_in_fragment(server):
    call, url, app, _ = server
    assert url.endswith(f"/#t={app.token}")  # il frammento non arriva mai al server
    status, page = call("GET", "/", token=None)
    assert status == 200 and b"Benvenuto in SoIA" in page


def test_api_requires_token_and_local_host(server):
    call, *_ = server
    assert call("POST", "/api/ask", {"text": "alza il volume"}, token=None)[0] == 403
    assert call("POST", "/api/ask", {"text": "alza il volume"}, token="sbagliato")[0] == 403
    # DNS rebinding: un sito che punta il suo dominio a 127.0.0.1 ha un Host diverso.
    assert call("POST", "/api/ask", {"text": "alza il volume"}, host="attacker.example:80")[0] == 403
    assert call("GET", "/", token=None, host="attacker.example")[0] == 403


def test_ask_runs_real_agent_with_fast_path_event(server):
    call, *_ = server
    status, body = call("POST", "/api/ask", {"text": "alza il volume"})
    assert status == 200
    state = wait_job(call, body["job"])
    assert state["answer"] == "Volume alzato."
    kinds = [e["kind"] for e in state["events"]]
    assert kinds == ["fast", "status"]


def test_confirmation_flow(server):
    call, _, _, powered = server
    job = call("POST", "/api/ask", {"text": "spegni"})[1]["job"]
    state = wait_job(call, job)
    assert state["pending"]["label"] == "⏻ Energia: spegni"
    assert powered == []  # niente senza il sì dell'utente
    assert call("POST", f"/api/job/{job}/confirm", {"ok": False})[1]["ok"]
    state = wait_job(call, job)
    assert state["answer"] == "Va bene, annullato." and powered == []


def test_without_model_answers_kindly(server):
    call, *_ = server
    job = call("POST", "/api/ask", {"text": "scrivi una poesia"})[1]["job"]
    assert "modello AI" in wait_job(call, job)["answer"]


def test_invalid_requests(server):
    call, *_ = server
    assert call("POST", "/api/ask", {"text": ""})[0] == 400
    assert call("POST", "/api/ask", {"text": "x" * 501})[0] == 400
    assert call("GET", "/api/job/999")[0] == 404


def test_profile_and_finish(server):
    call, _, app, _ = server
    status, body = call("POST", "/api/profile", {"name": "  Davide<script>42 ", "lang": "it"})
    assert body["profile"] == {"name": "Davidescript", "lang": "it"}
    assert call("GET", "/api/state")[1]["profile"]["name"] == "Davidescript"
    call("POST", "/api/finish", {})
    assert app.finished.is_set()
    assert welcome.load_profile()["welcome_done"] is True


def test_first_run_skips_when_done(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    welcome.save_profile(welcome_done=True)
    assert welcome.main(["--first-run"]) == 0  # ritorna subito, senza aprire nulla


def test_clean_name():
    assert welcome.clean_name("D'Angelo-Rossi") == "D'Angelo-Rossi"
    assert welcome.clean_name("Zoë") == "Zoë"
    assert welcome.clean_name(None) == ""
