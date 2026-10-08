import http.client
import json
from datetime import datetime, timedelta

import pytest

from aios_copilot.agenda import Agenda
from aios_copilot.agent import Agent
from aios_copilot.localapp import serve
from aios_copilot.mail.classify import classify
from aios_copilot.mail.store import MailStore
from aios_copilot.mail.ui import MailApp
from aios_copilot.tools import agenda as agenda_tools

NOW = datetime(2026, 10, 3, 9, 0)


def seed(store: MailStore, sender, name, subject, body, hours_ago=1, headers=None, sent_to=0, seen=False):
    v = classify(sender, subject, body, headers or {}, sent_to_count=sent_to)
    return store.add({"account": "me@x.it", "folder": "INBOX", "uidvalidity": 1, "uid": abs(hash(subject)) % 10**6,
                      "msgid": f"<{subject}>", "in_reply_to": "", "sender_name": name, "sender": sender,
                      "recipients": ["me@x.it"], "subject": subject, "date": NOW - timedelta(hours=hours_ago),
                      "snippet": body[:160], "body": body, "seen": int(seen), "attachments": [], "headers": headers or {},
                      "category": v.category, "importance": v.importance, "reason": v.reason})


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    store = MailStore(tmp_path / "mail.db")
    seed(store, "marco@cliente.it", "Marco Bianchi", "Urgente: firma del contratto",
         "Ciao, serve la firma entro il 5 ottobre. <script>alert(1)</script>", sent_to=3)
    seed(store, "news@shop.example", "Shop", "Sconti", "Offerta", headers={"list-unsubscribe": "x"}, seen=True)
    sent = []
    agenda = Agenda(tmp_path / "agenda.db", clock=lambda: NOW)

    def make_agent(confirm):
        tools = agenda_tools.make_tools(lambda: agenda, lambda: "", lambda: [])

        class NoModel:
            def chat(self, *a):
                raise AssertionError("niente LLM")

        return Agent(NoModel(), tools, confirm, routers=[agenda_tools.AgendaRouter(clock=lambda: NOW)])

    mail_app = MailApp(store, make_agent, agenda=agenda, accounts=lambda: [],
                       send=lambda to, s, b, r: sent.append((to, s, b, r)) or "Inviata.", clock=lambda: NOW)
    server, url = serve(mail_app)
    port = server.server_address[1]

    def call(method, path, body=None, token=mail_app.token):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {"Host": f"127.0.0.1:{port}", "X-AIOS-Token": token or "", "Content-Type": "application/json"})
        r = conn.getresponse()
        data = r.read()
        return r.status, (json.loads(data) if r.getheader("Content-Type", "").startswith("application/json") else data)

    yield call, store, agenda, sent
    server.shutdown()


def test_requires_token(app):
    call, *_ = app
    assert call("GET", "/api/folders", token="x")[0] == 403
    assert call("GET", "/", token=None)[0] == 200


def test_folders_list_and_read(app):
    call, store, *_ = app
    status, data = call("GET", "/api/folders")
    assert data["unread"] == 1 and data["important"][0]["subject"] == "Urgente: firma del contratto"
    _, data = call("GET", "/api/list?category=importanti")
    [m] = data["mails"]
    _, full = call("GET", f"/api/mail/{m['id']}")
    assert "<script>" in full["mail"]["body"]  # l'API restituisce il testo; la pagina lo mostra come testo
    assert full["dates"][0]["when"].startswith("2026-10-05")
    assert call("GET", "/api/list?q=contratto")[1]["mails"][0]["id"] == m["id"]


def test_user_actions(app):
    call, store, agenda, sent = app
    news = call("GET", "/api/list?category=newsletter")[1]["mails"][0]
    status, data = call("POST", f"/api/mail/{news['id']}/category", {"category": "notifiche"})
    assert data["ok"] and store.override_for("altro@shop.example") is None
    assert store.get(news["id"]).category == "notifiche"
    assert call("POST", "/api/agenda", {"title": "Firma contratto", "when": "2026-10-05T09:00"})[1]["message"].startswith("In agenda")
    assert [i.title for i in agenda.todos() + agenda.between(NOW, NOW + timedelta(days=5))] == ["Firma contratto"]
    assert call("POST", "/api/send", {"to": "marco@cliente.it", "subject": "Re", "body": "Firmato"})[0] == 200
    assert sent == [(["marco@cliente.it"], "Re", "Firmato", None)]
    assert call("POST", "/api/send", {"to": "nessuno", "body": "x"})[0] == 400


def test_copilot_knows_the_open_mail(app):
    call, store, agenda, _ = app
    m = store.search("firma")[0]
    job = call("POST", "/api/ask", {"text": "Ricordamelo domani alle 9", "mail_id": m.id})[1]["job"]
    for _ in range(100):
        state = call("GET", f"/api/job/{job}")[1]
        if state["done"]:
            break
    assert state["answer"] == "Fatto: ti ricorderò «Urgente: firma del contratto» domani alle 09:00."
