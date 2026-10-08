import json
import time

import pytest

from aios_copilot.agent import Agent
from aios_copilot.mesh import messages as msg
from aios_copilot.mesh.calls import Ofono
from aios_copilot.mesh.phone import KdeConnect
from aios_copilot.mesh.service import MeshService
from aios_copilot.tools import phone as phone_tools
from aios_copilot.tools.base import Runner

PHONE = "1a2b3c4d"
NOTIFS = {
    "1": {"appName": "WhatsApp", "title": "Giulia Rossi", "text": "Ci vediamo stasera alle 8?", "replyId": "r1", "dismissable": True},
    "2": {"appName": "Banca", "title": "Accesso", "text": "Il codice di verifica è 482913. Non condividerlo.", "replyId": "",
          "dismissable": True},
    "3": {"appName": "Amazon", "title": "Offerte", "text": "Sconti fino al 50%", "replyId": "", "dismissable": True},
    "4": {"appName": "Amazon", "title": "Pacco", "text": "In consegna oggi", "replyId": "", "dismissable": True},
}


def sms(body, number, ms, kind, read, thread):
    return {"type": "(isa(s)xiixia(xsss))", "data": [0, body, [[number]], ms, kind, read, thread, 1, 0, []]}


CONVERSATIONS = [sms("Arrivo tra 5 minuti", "+39 333 1234567", 1759480000000, 1, 0, 7),
                 sms("Ok grazie", "3479876543", 1759470000000, 2, 1, 9),
                 sms("Il tuo codice è 1234", "TIM", 1759460000000, 1, 1, 11)]


class FakePhone(Runner):
    def __init__(self, notifications=NOTIFS):
        super().__init__(which=lambda p: p if p in ("kdeconnect-cli", "busctl", "wl-copy") else None)
        self.notifications, self.ran = dict(notifications), []

    def run(self, cmd):
        self.ran.append(cmd)
        if cmd[:2] == ["kdeconnect-cli", "-l"]:
            return 0, f"- Pixel 8: {PHONE} (paired and reachable)\n"
        if cmd[:2] == ["busctl", "--user"]:
            if cmd[3] == "get-property":
                nid, name = cmd[5].rsplit("/", 1)[1], cmd[7]
                value = self.notifications[nid][name]
                return 0, json.dumps({"type": "b" if isinstance(value, bool) else "s", "data": value})
            method = cmd[7]
            if method == "activeNotifications":
                return 0, json.dumps({"type": "as", "data": [list(self.notifications)]})
            if method == "activeConversations":
                return 0, json.dumps({"type": "av", "data": [CONVERSATIONS]})
            return 0, json.dumps({"type": "", "data": []})
        return 0, ""


@pytest.fixture(autouse=True)
def contacts(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    book = tmp_path / "data/kpeoplevcard/kdeconnect-1a2b3c4d"
    book.mkdir(parents=True)
    (book / "a.vcf").write_text("BEGIN:VCARD\nVERSION:3.0\nFN:Marco Bianchi\nTEL;TYPE=CELL:+39 333 123 4567\nEND:VCARD\n"
                                "BEGIN:VCARD\nVERSION:3.0\nFN:Marta Verdi\nTEL:347 987 6543\nEND:VCARD\n")


def test_otp_codes():
    assert msg.otp_code("Il codice di verifica è 482913. Non condividerlo.") == "482913"
    assert msg.otp_code("G-123456 is your Google verification code") == "123456"
    assert msg.otp_code("123456 è il tuo codice WhatsApp") == "123456"
    assert msg.otp_code("Your code: 7781") == "7781"
    assert msg.otp_code("Ci vediamo alle 2030 in via Roma 12") == ""  # numeri qualsiasi non sono codici


def test_contacts_and_numbers():
    book = msg.load_contacts()
    assert book[msg.normalize_number("+393331234567")] == "Marco Bianchi"
    assert msg.find_number("marco", book) == (next(n for n, v in book.items() if v == "Marco Bianchi"), "Marco Bianchi")
    assert msg.find_number("mar", book) is None  # Marco o Marta? meglio chiedere
    assert msg.find_number("+39 340 0000000", book) == ("+39 340 0000000", "")


def test_notifications_are_grouped_and_codes_found():
    bus = msg.PhoneBus(FakePhone())
    notes = bus.notifications(PHONE)
    assert [n.kind for n in notes] == ["messaggio", "codice", "altro", "altro"]
    text = msg.summarize(notes)
    assert "🔑 Codice 482913 da Banca" in text and "Giulia Rossi (WhatsApp): Ci vediamo stasera alle 8?" in text
    assert "Altro: Amazon (2)" in text


def test_sms_conversations_with_names():
    convs = msg.PhoneBus(FakePhone()).conversations(PHONE, msg.load_contacts())
    assert [(m.who, m.incoming, m.read) for m in convs] == [("Marco Bianchi", True, False), ("Marta Verdi", False, True),
                                                           ("TIM", True, True)]


class NoModel:
    def chat(self, *a):
        raise AssertionError("niente LLM")


def make_agent(runner, confirmed):
    tools = phone_tools.make_tools(runner)
    return Agent(NoModel(), tools, confirm=lambda tool, args, **k: confirmed.append((tool.name, args)) or True,
                 routers=[phone_tools.PhoneRouter()])


def test_copilot_reads_and_answers_from_the_pc():
    r, confirmed = FakePhone(), []
    agent = make_agent(r, confirmed)
    assert "Giulia Rossi" in agent.ask("cosa c'è sul telefono?")
    assert agent.ask("copia il codice").startswith("Codice 482913 (Banca) copiato")
    assert ["wl-copy", "482913"] in r.ran

    assert agent.ask("Rispondi a Giulia su WhatsApp: Sì, alle 8 va benissimo!") == "Risposto a Giulia Rossi su WhatsApp."
    assert confirmed[-1] == ("reply_message", {"who": "giulia", "text": "Sì, alle 8 va benissimo!"})  # sempre con conferma
    assert r.ran[-1][-4:] == ["sendReply", "ss", "r1", "Sì, alle 8 va benissimo!"]

    out = agent.ask("leggi gli sms")
    assert "🆕" in out and "da Marco Bianchi: Arrivo tra 5 minuti" in out and "a Marta Verdi: Ok grazie" in out
    assert "Marco" not in agent.ask("sms di marta")

    assert agent.ask("Manda un SMS a Marco: Sono in ritardo, scusa!") == "SMS inviato a Marco Bianchi."
    assert confirmed[-1][0] == "send_sms"
    assert r.ran[-1] == ["kdeconnect-cli", "-d", PHONE, "--send-sms", "Sono in ritardo, scusa!", "--destination",
                         next(n for n, v in msg.load_contacts().items() if v == "Marco Bianchi")]
    assert "Non trovo un solo contatto" in agent.ask("manda un sms a mar: ciao")


def test_privacy_gate_applies_to_phone_messages():
    tools = {t.name: t for t in phone_tools.make_tools(FakePhone())}
    assert tools["phone_notifications"].reads_private and tools["read_sms"].reads_private
    assert tools["send_sms"].requires_confirmation and tools["send_sms"].sends_out
    assert tools["reply_message"].requires_confirmation and tools["reply_message"].sends_out


def test_service_offers_to_copy_codes():
    r = FakePhone()
    asked, copied = [], []
    svc = MeshService(KdeConnect(r), Ofono(r), type("S", (), {"running": True, "pairing": type("P", (), {"code": ""})(),
                                                              "start": lambda s, **k: None, "stop": lambda s: None})(),
                      notify=lambda t, b: None, ask=lambda t, b, a: asked.append(t) or "copia",
                      bus=msg.PhoneBus(r), copy=copied.append)
    svc.tick()
    deadline = time.time() + 2
    while not copied and time.time() < deadline:
        time.sleep(0.02)
    assert asked == ["🔑 Codice 482913"] and copied == ["482913"]
    svc.tick()
    time.sleep(0.05)
    assert len(asked) == 1  # lo stesso codice non si propone due volte
