from datetime import date

from aios_copilot import pianifica as PI
from aios_copilot.agent import Agent
from aios_copilot.tools.base import Tool, params
from aios_copilot.tools.calcolo import calculate, evaluate

OGGI = date(2026, 10, 5)


def test_calculator_does_the_sums_and_the_dates():
    assert calculate("84,20 + 91,10 + 78,50", OGGI).endswith("= 253,80")
    assert calculate("media(84,20; 91,10; 78,50)", OGGI).endswith("= 84,60")
    assert calculate("84,20 - 78,50", OGGI).endswith("= 5,70")
    assert calculate("giorni tra oggi e 15 novembre 2026", OGGI).endswith("= 41")
    assert calculate("giorni tra 2026-10-05 e 2026-11-15", OGGI).endswith("= 41")
    assert calculate("0,1 + 0,2", OGGI).endswith("= 0,3")
    assert calculate("__import__('os').system('x')", OGGI).startswith("Errore")
    assert calculate("9 ** 9999", OGGI).startswith("Errore") and calculate("1/0", OGGI).startswith("Errore")
    assert evaluate("(78,50+41,60+29,90)") == 150


def test_plan_is_parsed_and_only_for_real_tasks():
    assert PI.parse_plan("Ecco il piano:\n1. cerco le bollette con search_files\n2) leggo gli importi\n- calcolo con calculate") == [
        "cerco le bollette con search_files", "leggo gli importi", "calcolo con calculate"]
    assert PI.wants_plan("quanto ho speso di luce e gas negli ultimi tre mesi?")
    assert PI.wants_plan("metti in agenda la riunione che mi ha proposto Giulia")
    assert not PI.wants_plan("che ore sono?") and not PI.wants_plan("raccontami una barzelletta divertente")


def test_guards_stop_actions_that_are_surely_wrong():
    assert PI.guard("manda a Sara quanto ho speso di bollette", "send_email", {"to": "sara", "body": "Ecco le bollette"}, OGGI)
    assert PI.guard("manda a Sara quanto ho speso di bollette", "send_email", {"to": "sara", "body": "In tutto 150 euro"}, OGGI) is None
    assert "già passato" in PI.guard("ricordami di pagare la luce", "add_reminder", {"what": "luce", "when": "2026-08-20"}, OGGI)
    assert PI.guard("ricordami di pagare la luce", "add_reminder", {"what": "luce", "when": "2026-10-20T09:00"}, OGGI) is None


def test_verify_catches_what_is_missing():
    tools = {"search_mail", "read_mail", "add_event", "calculate", "send_email"}
    assert "agenda" in PI.verify("metti in agenda la riunione di Giulia", "Fatto!", ["search_mail"], tools, 0)
    assert "cercato" in PI.verify("metti in agenda la riunione che mi ha proposto Giulia",
                                  "Quando vuole fare la riunione?", [], {"search_mail", "add_event"} - {"add_event"}, 0)
    assert "calculate" in PI.verify("qual è la differenza tra luglio e settembre?", "La differenza è 5,7 euro", ["read_file"], tools, 0)
    assert "numero" in PI.verify("quanto ho speso a settembre?", "Hai speso un po'.", ["read_file"], tools, 0)
    assert PI.verify("quanto ho speso a settembre?", "150 euro", ["read_file", "calculate"], tools, 0) is None
    assert PI.verify("metti in agenda la riunione", "Fatto", [], tools, PI.MAX_CHECKS) is None  # non all'infinito


class Script:
    """Un modello finto che segue un copione: il piano, poi le chiamate e le risposte."""

    supports_stream = False

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []
        self.think = False

    def chat(self, messages, tools):
        self.seen.append(messages[-1]["content"])
        return self.replies.pop(0)


def call(name, **args):
    return {"tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_agent_plans_prefetches_guards_and_verifies():
    sent, events = [], []
    mails = "[5] da Giulia Neri: Riunione progetto"
    tools = [
        Tool("search_mail", "cerca mail", params(query="q"), lambda query="": mails, reads_private=True),
        Tool("read_mail", "leggi", params(mail_id="id"), lambda mail_id="": "Giovedì 8 ottobre alle 15 in sala blu", reads_private=True),
        Tool("add_event", "agenda", params(title="t", when="w"), lambda title="", when="": sent.append(when) or f"Aggiunto {title}"),
    ]
    model = Script([
        {"content": "1. leggo la mail di Giulia con read_mail\n2. aggiungo la riunione con add_event"},  # il piano
        {"content": "Quando è la riunione?"},  # chiede invece di fare: la verifica lo rimanda al lavoro
        call("read_mail", mail_id="5"),
        call("add_event", title="Riunione", when="2026-09-01T15:00"),  # data passata: la guardia la ferma
        call("add_event", title="Riunione progetto", when="2026-10-08T15:00"),
        {"content": "Fatto: riunione giovedì 8 alle 15."},
    ])
    agent = Agent(model, tools, confirm=lambda *a, **k: True, max_steps=10,
                  pianificatore=PI.Planner(model, today=lambda: OGGI))
    answer = agent.ask("metti in agenda la riunione che mi ha proposto Giulia", on_event=lambda k, d: events.append(k))
    assert answer == "Fatto: riunione giovedì 8 alle 15."
    assert sent == ["2026-10-08T15:00"]  # la data passata non è mai arrivata all'agenda
    assert "prefetch" in events and "plan" in events and "retry" in events
    assert "Giulia Neri" in model.seen[1] and "Piano da seguire" in model.seen[1]
    assert any("Controllo automatico" in s for s in model.seen)


def test_agent_without_planner_is_unchanged():
    model = Script([{"content": "Ciao!"}])
    assert Agent(model, [], confirm=lambda *a, **k: True).ask("ciao come va") == "Ciao!"
