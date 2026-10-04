import threading
import time

from aios_copilot.agent import Agent, Intent
from aios_copilot.localapp import LocalApp
from aios_copilot.tools import Tool


class SlowModel:
    """Il modello che ragiona a lungo sulla prima domanda."""

    supports_stream = False

    def __init__(self):
        self.go = threading.Event()
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            assert self.go.wait(5)
            return {"content": "420"}
        return {"content": "Ti consiglio Dark."}


class Volume:
    def match(self, text):
        return Intent("set_volume", {"level": 30}) if "volume" in text else None


def wait(job, seconds=5):
    end = time.monotonic() + seconds
    while job.answer is None and time.monotonic() < end:
        time.sleep(0.01)
    return job.answer


def test_commands_run_while_the_model_thinks_and_questions_wait_in_line():
    model = SlowModel()
    vol = Tool("set_volume", "Volume", {"type": "object", "properties": {"level": {"type": "integer"}}},
               lambda level: f"Volume al {level}%.")
    app = LocalApp(lambda confirm: Agent(model, [vol], confirm, routers=[Volume()]))
    first = app.jobs[app.start_job("quanto fa 17 per 23 più 145 diviso 5?")]
    time.sleep(0.1)
    command = app.jobs[app.start_job("abbassa il volume al 30")]
    assert wait(command, 2) == "Volume al 30%."  # subito, mentre il modello è ancora sulla prima
    assert first.answer is None
    question = app.jobs[app.start_job("mi consigli una serie?")]
    time.sleep(0.1)
    assert question.answer is None and any(e["kind"] == "coda" for e in question.events)
    model.go.set()
    assert wait(first) == "420" and wait(question) == "Ti consiglio Dark."
    # il comando fatto nel frattempo entra nella conversazione, dopo la prima richiesta
    said = [m["content"] for m in app.agent.messages if m["role"] == "user"]
    assert said == ["quanto fa 17 per 23 più 145 diviso 5?", "abbassa il volume al 30", "mi consigli una serie?"]
