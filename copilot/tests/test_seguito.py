"""Le risposte che dipendono da quello che si è appena detto («collegalo», «sì», «aprilo»)."""

from aios_copilot.agent import Agent
from aios_copilot.fastpath import Intent
from aios_copilot.tools.base import Tool, offer, params
from aios_copilot.tools.mail import MailRouter


class Router:
    def __init__(self, table):
        self.table = table

    def match(self, text):
        return self.table.get(text)


class Model:
    def __init__(self):
        self.seen = []

    def chat(self, messages, tools, **kw):
        self.seen.append(([m["content"] for m in messages if m["role"] == "user"], [t["function"]["name"] for t in tools]))
        return {"content": "fatto"}


def make(model, ran):
    def overview():
        offer("collega la posta")
        return "Non hai ancora collegato la posta. Vuoi farlo adesso?"

    tools = [Tool("mail_overview", "Posta", params(), overview),
             Tool("connect_mail", "Collega", params(), lambda: ran.append("connect_mail") or "Ecco la schermata."),
             Tool("set_radio", "Wi-Fi", params(device="d", state="s"), lambda device, state: ran.append("wifi") or "Wi-Fi attivato.")]
    routers = [Router({"che mail devo leggere?": Intent("mail_overview", {}), "collega la posta": Intent("connect_mail", {}),
                       "collegalo": Intent("set_radio", {"device": "wifi", "state": "on"})})]
    return Agent(model, tools, confirm=lambda *a, **k: True, routers=routers)


def test_yes_to_an_offer_runs_what_was_offered():
    ran = []
    agent = make(Model(), ran)
    agent.ask("che mail devo leggere?")
    assert agent.ask("collegalo") == "Ecco la schermata." and ran == ["connect_mail"]  # non il Wi-Fi
    agent.ask("che mail devo leggere?")
    agent.ask("sì")
    assert ran == ["connect_mail", "connect_mail"]


def test_offer_lasts_one_turn_and_needs_the_same_verb():
    ran = []
    agent = make(Model(), ran)
    agent.ask("che mail devo leggere?")
    agent.ask("spegnilo")  # un altro verbo: non è un sì alla proposta
    assert "connect_mail" not in ran
    agent.ask("sì")  # la proposta valeva una volta sola
    assert "connect_mail" not in ran


def test_partial_sentence_goes_to_the_model_with_context():
    ran, model = [], Model()
    agent = make(model, ran)
    agent.ask("collega la posta")
    agent.ask("collegalo")  # nessuna proposta: frase a metà → niente regole veloci, la capisce il modello
    assert ran == ["connect_mail"] and model.seen and model.seen[-1][0][-2:] == ["collega la posta", "collegalo"]


def test_connect_mail_phrases():
    r = MailRouter()
    for text in ["collega la posta", "aggiungi un account email", "configura la mia mail", "collegami la casella di posta"]:
        assert r.match(text) == Intent("connect_mail", {}), text
