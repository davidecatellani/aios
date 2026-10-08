import time

import pytest

from aios_copilot.agent import Agent
from aios_copilot.semantic import OllamaEncoder, SemanticRouter, concepts
from aios_copilot.status import describe_call
from aios_copilot.tools import Tool
from aios_copilot.tools.base import params

from semantic_eval import OUT_OF_SCOPE, evaluate


@pytest.fixture(scope="module")
def router():
    return SemanticRouter()


def test_quality_on_held_out_sentences(router):
    report = evaluate(router)
    assert report["false_accepts"] == []  # mai eseguire una richiesta fuori tema
    assert report["precision"] >= 0.97
    assert report["coverage"] >= 0.85


def test_speed_on_cpu(router):
    start = time.perf_counter()
    for text in OUT_OF_SCOPE * 5:
        router.match(text)
    assert (time.perf_counter() - start) / (len(OUT_OF_SCOPE) * 5) < 0.005


@pytest.mark.parametrize(
    "text, args",
    [
        ("riduci un po' il volume", {"action": "down"}),
        ("il volume è troppo basso", {"action": "up"}),
        ("è troppo forte", {"action": "down"}),
    ],
)
def test_direction_and_too(router, text, args):
    assert router.match(text).args == args


def test_negation_and_unknown_words_abstain(router):
    assert router.match("spegni il wifi") is not None
    assert router.match("non spegnere il wifi") is None
    assert router.match("spegni il computer di marco") is None
    assert router.match("sube el volumen") is None  # lingua non coperta: decide l'LLM


def test_concepts_keep_opposites_apart():
    assert concepts("disattiva") == ["OFF"]
    assert concepts("attivami") == ["ON"]
    assert concepts("wifi") == ["WIFI"]


class FakeEmbedder(OllamaEncoder):
    """Embedding finto: vettore di presenza di alcune parole chiave."""

    KEYS = ["alza", "abbassa", "volume", "wifi", "spegni"]

    def __init__(self):
        super().__init__("finto", threshold=0.8, margin=0.05)

    def _embed(self, texts):
        out = []
        for t in texts:
            v = [1.0 if k in t else 0.0 for k in self.KEYS]
            n = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / n for x in v])
        return out


def test_pluggable_neural_encoder():
    router = SemanticRouter(encoder=FakeEmbedder())
    assert router.match("alza il volume").args == {"action": "up"}


class NoModel:
    def chat(self, messages, tools):
        raise AssertionError("il livello 1 non deve interpellare l'LLM")


def test_agent_uses_level_one():
    events = []
    tools = [Tool("set_volume", "", params(action="a"), lambda action: f"volume {action}")]
    agent = Agent(NoModel(), tools, confirm=lambda t, a: True, routers=[SemanticRouter()])
    assert agent.ask("si sente troppo piano", lambda k, d: events.append((k, d))) == "volume up"
    assert events[0] == ("routed", {"intent": events[0][1]["intent"], "level": 0})


def test_confirmation_labels_are_italian():
    tool = Tool("power", "", params(action="a"), lambda action: "", requires_confirmation=True)
    assert describe_call(tool, {"action": "poweroff"}) == "⏻ Energia: spegni"
