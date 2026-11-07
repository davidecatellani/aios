import json
from datetime import datetime, timedelta

from aios_copilot.mail import livelli, service
from aios_copilot.mail.classify import classify
from aios_copilot.mail.store import MailStore
from aios_copilot.tools.mail import MailRouter, make_tools

NOW = datetime(2026, 10, 3, 9, 0)


def seed(store, sender, name, subject, body, days_ago=1, headers=None):
    v = classify(sender, subject, body, headers or {})
    return store.add({"account": "me@x.it", "folder": "INBOX", "uidvalidity": 1, "uid": abs(hash(subject)) % 10**6,
                      "msgid": f"<{subject}>", "in_reply_to": "", "sender_name": name, "sender": sender,
                      "recipients": ["me@x.it"], "subject": subject, "date": NOW - timedelta(days=days_ago),
                      "snippet": body[:160], "body": body, "seen": 0, "attachments": [], "headers": headers or {},
                      "category": v.category, "importance": v.importance, "reason": v.reason})


class Reader:
    """Il modello finto: legge la mail come farebbe il 4B."""

    def __init__(self):
        self.read = []

    def chat(self, messages, tools):
        text = messages[-1]["content"]
        self.read.append(text.splitlines()[0])
        if "report" in text:
            return {"content": '{"tipo": "da fare", "cosa": "mandare il report", "entro": "12 ottobre"}'}
        if "verbale" in text:
            return {"content": 'Ecco: {"tipo": "Da fare", "cosa": "firmare il verbale", "entro": "entro il 5/10"}'}
        if "foto" in text:
            return {"content": json.dumps({"tipo": "da fare", "cosa": "", "entro": ""})}  # senza cosa: l'oggetto
        return {"content": '{"tipo": "informazione", "cosa": "", "entro": ""}'}


def box(tmp_path):
    store = MailStore(tmp_path / "mail.db")
    seed(store, "paolo.bianchi@acme.it", "Paolo Bianchi", "Report", "Ciao, mi mandi il report entro il 12 ottobre?")
    seed(store, "giulia@gmail.com", "Giulia Neri", "Verbale", "Mi firmi il verbale entro il 5/10? Grazie")
    seed(store, "zia@libero.it", "Zia Anna", "Le foto", "Mandami le foto del matrimonio quando puoi")
    seed(store, "luca@gmail.com", "Luca Verdi", "Ciao", "Siamo tornati dalle vacanze, tutto bene!")
    seed(store, "offerte@zalando.it", "Zalando", "Saldi", "Sconti fino al 50%", headers={"list-unsubscribe": "<x>"})
    seed(store, "vecchia@gmail.com", "Vecchio", "Report vecchio", "il report entro il 12 ottobre", days_ago=90)
    return store


def test_silver_and_gold(tmp_path):
    store = box(tmp_path)
    model = Reader()
    waits = []
    assert livelli.update(store, model, wait=lambda: waits.append(1), now=NOW) == 5  # la mail di 90 giorni fa no
    assert len(model.read) == 4 and len(waits) == 4  # la pubblicità la decide il codice, senza modello
    assert livelli.update(store, model, now=NOW) == 0  # già letto: niente da rifare
    items = livelli.todo(store, NOW)
    assert [(i["chi"], i["entro"]) for i in items] == [("Giulia Neri", "2026-10-05"), ("Paolo Bianchi", "2026-10-12"),
                                                        ("Zia Anna", "")]
    assert items[2]["cosa"] == "Le foto"
    assert [i["chi"] for i in livelli.todo(store, NOW, days=3)] == ["Giulia Neri"]
    assert [i["chi"] for i in livelli.todo(store, NOW + timedelta(days=4))] == ["Paolo Bianchi", "Zia Anna"]  # scaduta
    text = livelli.todo_text(store, NOW)
    assert "1. Giulia Neri: firmare il verbale, entro il 5 ottobre" in text and "Zalando" not in text and "Luca" not in text
    assert "non ho letto" not in text


def test_due_date_and_unread_note(tmp_path):
    received = datetime(2026, 12, 20)
    assert livelli.due_date("10 gennaio", received) == "2027-01-10"  # l'anno dopo
    assert livelli.due_date("18 dicembre", received) == "2026-12-18"
    assert livelli.due_date("presto", received) == "" and livelli.due_date("31/02", received) == ""
    store = box(tmp_path)

    class Off:
        def chat(self, messages, tools):
            raise OSError("spento")

    assert livelli.update(store, Off(), now=NOW) == 1  # la pubblicità sì (le più recenti prima), poi ci si ferma
    assert "Ci sono ancora 4 mail che non ho letto" in livelli.todo_text(store, NOW)


def test_nova_tool_router_and_service(tmp_path, monkeypatch):
    store = box(tmp_path)
    livelli.update(store, Reader(), now=NOW)
    monkeypatch.setattr(livelli, "datetime", type("D", (), {"now": staticmethod(lambda: NOW)}))
    tools = {t.name: t for t in make_tools(lambda: store, lambda *a: "", lambda: True)}
    assert tools["mail_todo"].func().startswith("Le cose da fare dalla posta:")
    assert "prossimi 7 giorni" in tools["mail_todo"].func(giorni="7")
    assert "Da fare questa settimana: Giulia Neri: firmare il verbale." in tools["mail_overview"].func()
    r = MailRouter()
    assert r.match("cosa mi chiedono nelle mail?").tool == "mail_todo"
    assert r.match("le cose da fare dalla posta di questa settimana").args == {"giorni": "7"}
    assert r.match("scadenze nelle mail nei prossimi 10 giorni").args == {"giorni": "10"}
    assert r.match("cosa devo fare oggi") is None  # resta all'agenda
    calls = []
    monkeypatch.setattr(service, "sync_all", lambda s, a=None: ([], []))
    monkeypatch.setattr(service.client, "load_accounts", lambda: [])
    service.run_service(store, lambda t, b: None, once=True, facts=lambda s: calls.append(s) or 0)
    assert calls == [store]
